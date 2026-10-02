"""Passo appunti: trascrizioni/<stem>.md → appunti/<stem>_appunti.md (refiner + dispense, modelli per ruolo)."""

from __future__ import annotations

import shutil

from sbob.core import frontmatter, naming
from sbob.core.batch import Job, atomic_write_text, list_inputs, plan_jobs
from sbob.core.report import StepReport
from sbob.core.text import strip_timestamps
from sbob.llm.cost import CostTracker
from sbob.llm.registry import Registry, parse_model_override
from sbob.steps.base import StepContext
from sbob.steps.notes.chunker import DEFAULT_CHUNK_WORDS, chunk_text
from sbob.steps.notes.pipeline import ChunkCache, generate_notes


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("appunti")
    lay = ctx.layout
    todo, done = plan_jobs(list_inputs(lay.trascrizioni, [".md"]),
                           lambda p: lay.appunti / f"{p.stem}{naming.NOTES_SUFFIX}.md",
                           force=ctx.force, only=ctx.only)
    rep.skipped = [j.src.stem for j in done]
    chunk_words = int(ctx.settings.raw.get("appunti", {}).get("parole_per_blocco", DEFAULT_CHUNK_WORDS))

    if ctx.dry_run:
        rep.done = [j.src.stem for j in todo]
        words = sum(len(frontmatter.read(j.src)[1].split()) for j in todo)
        chunks = sum(len(chunk_text(frontmatter.read(j.src)[1], chunk_words)) for j in todo)
        rep.notes.append(f"{words} parole in {chunks} blocchi: {chunks} chiamate refiner + {chunks} chiamate notes")
        return rep
    if not todo:
        return rep

    tracker = CostTracker(log_path=lay.costs)
    reg = Registry(ctx.settings, tracker)
    refiner = reg.role("refiner", parse_model_override(ctx.options.get("refiner")))
    notes = reg.role("notes", parse_model_override(ctx.options.get("notes")))
    lay.ensure("appunti")
    try:
        for job in todo:
            _process(ctx, job, refiner, notes, chunk_words, rep)
    finally:
        rep.cost = tracker.summary()
    return rep


def _process(ctx: StepContext, job: Job, refiner, notes, chunk_words: int, rep: StepReport) -> None:
    stem = job.src.stem
    tmeta, raw = frontmatter.read(job.src)
    if not raw.strip():
        rep.fail(stem, "trascrizione vuota")
        return
    cache_dir = ctx.layout.state / "cache" / stem
    if ctx.force:
        shutil.rmtree(cache_dir, ignore_errors=True)
    raw = strip_timestamps(raw)                 # i [mm:ss] restano nella trascrizione, non negli appunti
    text, topics, errors = generate_notes(raw, refiner, notes, ctx.course.lingua, stem, chunk_words, ctx.log,
                                          cache=ChunkCache(cache_dir))
    partial = job.dst.with_name(f"{stem}.parziale.md")
    if errors:
        atomic_write_text(partial, text)
        rep.fail(stem, f"{len(errors)} blocchi falliti, salvato parziale: {partial.name} ({errors[0]})")
        return
    modello = notes.model + (f" (+ riserva {notes.used_fallback})" if notes.used_fallback else "")
    meta = frontmatter.lesson_meta(ctx.course, stem, argomento=tmeta.get("argomento"), backend=tmeta.get("backend"),
                                   modello=modello,
                                   refiner=refiner.model)   # concetti e riassunti stanno in mappa/, non qui
    atomic_write_text(job.dst, frontmatter.join(meta, text))
    partial.unlink(missing_ok=True)
    shutil.rmtree(cache_dir, ignore_errors=True)
    rep.done.append(stem)
    rep.outputs.append(str(job.dst))
