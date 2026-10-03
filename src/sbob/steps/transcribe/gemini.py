"""Backend gemini (default): carica l'audio sulla Files API e chiede la trascrizione al ruolo `trascrizione`."""

from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sbob.core import media, prompts
from sbob.core.text import shift_timestamps
from sbob.core.batch import Job
from sbob.core.report import NeedsHuman, StepReport
from sbob.llm.base import FilePart, Message
from sbob.llm.cost import CostTracker
from sbob.llm.registry import Registry, Role, parse_model_override
from sbob.steps.base import StepContext
from sbob.steps.notes.pipeline import NO_CACHE, ChunkCache
from sbob.steps.transcribe.base import AUDIO_NATIVE, write_transcript

SEGMENT_SECONDS = 45 * 60   # usato solo se la risposta intera viene troncata (finish_reason=length)


def _ask(role: Role, audio: Path, mime: str, prompt: str, item: str):
    res = role.complete([Message.user(FilePart(audio, mime), prompt)], item=item, allow_truncated=True,
                        validate=lambda t: None if t.strip() else "testo vuoto")
    if not res.ok:
        raise RuntimeError(res.error or "trascrizione fallita")
    return res


def _cached_ask(role: Role, cache: ChunkCache, kind: str, i: int, audio: Path, mime: str, prompt: str, item: str):
    """Come _ask, ma un risultato completo già pagato (es. un segmento riuscito prima che finisse la quota) si riusa."""
    size = audio.stat().st_size if audio.exists() else 0
    key = f"{audio.name}:{size}\0{prompt}"
    if (hit := cache.get(kind, i, role.model, key)) is not None:
        return hit, "stop"
    res = _ask(role, audio, mime, prompt, item)
    if res.finish_reason != "length":
        cache.put(kind, i, role.model, key, res.text)
    return res.text, res.finish_reason


def transcribe_file(role: Role, audio: Path, prompt: str, workdir: Path, item: str, cache: ChunkCache = NO_CACHE) -> str:
    if audio.suffix.lower() not in AUDIO_NATIVE:
        audio = media.to_m4a(audio, workdir)
    mime = AUDIO_NATIVE[audio.suffix.lower()]
    text, finish = _cached_ask(role, cache, "full", 0, audio, mime, prompt, item)
    if finish != "length":
        return text
    # risposta troncata: audio troppo lungo per un'unica risposta → a segmenti
    parts = media.split_audio(audio, workdir, SEGMENT_SECONDS)
    if len(parts) < 2:
        return text
    texts, offset = [], 0.0
    for i, seg in enumerate(parts, 1):
        seg_text, seg_finish = _cached_ask(role, cache, "seg", i, seg, mime, prompt, f"{item}#{i}")
        if seg_finish == "length":
            raise RuntimeError(f"segmento {i}/{len(parts)} ancora troncato: abbassa SEGMENT_SECONDS")
        texts.append(shift_timestamps(seg_text.strip(), offset))   # i [mm:ss] del segmento partono da 0
        offset += media.duration_seconds(seg) or SEGMENT_SECONDS
    return "\n\n".join(texts)


def transcribe_many(ctx: StepContext, jobs: list[Job], rep: StepReport) -> None:
    tracker = CostTracker(log_path=ctx.layout.costs)
    role = Registry(ctx.settings, tracker).role("trascrizione", parse_model_override(ctx.options.get("modello")))
    prompt = prompts.load(ctx.course.lingua, "transcriber")
    # timestamp spenti di default (scelta dell'utente): su audio lunghi sono radi e sfasati di minuti; si riaccendono
    # con `[trascrizione] timestamp = true`
    if ctx.settings.raw.get("trascrizione", {}).get("timestamp", False):
        prompt += prompts.load(ctx.course.lingua, "transcriber_timestamp")
    workers = max(1, min(role.n_keys, 4))

    def one(job: Job) -> tuple[Job, str]:
        cache_dir = ctx.layout.state / "cache" / f"trascrizione_{job.dst.stem}"
        with tempfile.TemporaryDirectory() as tmp:
            return job, transcribe_file(role, job.src, prompt, Path(tmp), job.dst.stem, ChunkCache(cache_dir))

    ctx.log(f"trascrizione: {len(jobs)} file con {role.label} ({workers} paralleli)")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, j): j for j in jobs}
        try:
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    _, text = fut.result()
                except NeedsHuman:
                    raise
                except Exception as e:  # noqa: BLE001
                    rep.fail(job.dst.stem, e)
                    continue
                ctx.log(f"trascrizione: {job.dst.stem} ok")
                write_transcript(ctx, job, text, rep, backend="gemini", model=role.model)
                shutil.rmtree(ctx.layout.state / "cache" / f"trascrizione_{job.dst.stem}", ignore_errors=True)   # riuscita: la cache non serve più
        except BaseException:
            for f in futures:
                f.cancel()
            raise
        finally:
            rep.cost = tracker.summary()
