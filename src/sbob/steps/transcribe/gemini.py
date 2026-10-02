"""Backend gemini (default): carica l'audio sulla Files API e chiede la trascrizione al ruolo `trascrizione`."""

from __future__ import annotations

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
from sbob.steps.transcribe.base import AUDIO_NATIVE, write_transcript

SEGMENT_SECONDS = 45 * 60   # usato solo se la risposta intera viene troncata (finish_reason=length)


def _ask(role: Role, audio: Path, mime: str, prompt: str, item: str):
    res = role.complete([Message.user(FilePart(audio, mime), prompt)], item=item, allow_truncated=True,
                        validate=lambda t: None if t.strip() else "testo vuoto")
    if not res.ok:
        raise RuntimeError(res.error or "trascrizione fallita")
    return res


def transcribe_file(role: Role, audio: Path, prompt: str, workdir: Path, item: str) -> str:
    if audio.suffix.lower() not in AUDIO_NATIVE:
        audio = media.to_m4a(audio, workdir)
    mime = AUDIO_NATIVE[audio.suffix.lower()]
    res = _ask(role, audio, mime, prompt, item)
    if res.finish_reason != "length":
        return res.text
    # risposta troncata: audio troppo lungo per un'unica risposta → a segmenti
    parts = media.split_audio(audio, workdir, SEGMENT_SECONDS)
    if len(parts) < 2:
        return res.text
    texts, offset = [], 0.0
    for i, seg in enumerate(parts, 1):
        r = _ask(role, seg, mime, prompt, f"{item}#{i}")
        if r.finish_reason == "length":
            raise RuntimeError(f"segmento {i}/{len(parts)} ancora troncato: abbassa SEGMENT_SECONDS")
        texts.append(shift_timestamps(r.text.strip(), offset))   # i [mm:ss] del segmento partono da 0
        offset += media.duration_seconds(seg) or SEGMENT_SECONDS
    return "\n\n".join(texts)


def transcribe_many(ctx: StepContext, jobs: list[Job], rep: StepReport) -> None:
    tracker = CostTracker(log_path=ctx.layout.costs)
    role = Registry(ctx.settings, tracker).role("trascrizione", parse_model_override(ctx.options.get("modello")))
    prompt = prompts.load(ctx.course.lingua, "transcriber")
    if ctx.settings.raw.get("trascrizione", {}).get("timestamp", True):
        prompt += prompts.load(ctx.course.lingua, "transcriber_timestamp")
    workers = max(1, min(role.n_keys, 4))

    def one(job: Job) -> tuple[Job, str]:
        with tempfile.TemporaryDirectory() as tmp:
            return job, transcribe_file(role, job.src, prompt, Path(tmp), job.dst.stem)

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
        except BaseException:
            for f in futures:
                f.cancel()
            raise
        finally:
            rep.cost = tracker.summary()
