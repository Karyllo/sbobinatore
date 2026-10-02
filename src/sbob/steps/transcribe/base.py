"""Contratto dei backend di trascrizione: `transcribe_many(ctx, jobs, rep)`.

Ogni backend scrive i risultati con `write_transcript` (frontmatter uniforme, scrittura atomica) e
registra l'esito per file in `rep` (done / failed). Può sollevare NeedsHuman per fermare tutto.
"""

from __future__ import annotations

from sbob.core import frontmatter
from sbob.core.batch import Job, atomic_write_text
from sbob.core.report import StepReport
from sbob.steps.base import StepContext

AUDIO_NATIVE = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
                ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac", ".aiff": "audio/aiff"}


def write_transcript(ctx: StepContext, job: Job, text: str, rep: StepReport, *, backend: str,
                     model: str | None = None) -> None:
    text = text.strip()
    if not text:
        rep.fail(job.dst.stem, "trascrizione vuota")
        return
    argomento = ctx.manifest().data["meta"].get(job.dst.stem, {}).get("argomento")
    meta = frontmatter.lesson_meta(ctx.course, job.dst.stem, argomento=argomento, sorgente=job.src.name,
                                   backend=backend, modello=model)
    atomic_write_text(job.dst, frontmatter.join(meta, text + "\n"))
    rep.done.append(job.dst.stem)
    rep.outputs.append(str(job.dst))
