"""Passo audio: video/ → audio/ (.aac mono-parlato, bitrate da config)."""

from __future__ import annotations

import shutil
import subprocess

from sbob.core.batch import list_inputs, plan_jobs
from sbob.core.report import NeedsHuman, StepReport
from sbob.core.status import VIDEO_EXT
from sbob.steps.base import StepContext


def _extract(src, dst, bitrate: str) -> None:
    tmp = dst.with_name(f".{dst.stem}.tmp.aac")
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", bitrate, str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "ffmpeg fallito")
    tmp.replace(dst)


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("audio")
    if not shutil.which("ffmpeg"):
        raise NeedsHuman("ffmpeg non trovato", action="brew install ffmpeg")
    lay = ctx.layout
    todo, done = plan_jobs(list_inputs(lay.video, VIDEO_EXT), lambda p: lay.audio / f"{p.stem}.aac",
                           force=ctx.force, only=ctx.only)
    rep.skipped = [j.src.stem for j in done]
    if ctx.dry_run:
        rep.done = [j.src.stem for j in todo]
        return rep
    lay.ensure("audio")
    for job in todo:
        ctx.log(f"audio: {job.src.name}")
        try:
            _extract(job.src, job.dst, ctx.settings.audio_bitrate)
        except Exception as e:  # noqa: BLE001
            rep.fail(job.src.stem, e)
            continue
        rep.done.append(job.src.stem)
        rep.outputs.append(str(job.dst))
    return rep
