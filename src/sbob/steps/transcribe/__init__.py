"""Passo trascrivi: audio/ → trascrizioni/<stem>.md.

Backend (course.trascrizione o --backend): gemini (default) · notebooklm · html · whisper (non mantenuto) (import di export manuali).
"""

from __future__ import annotations

import importlib
from pathlib import Path

from sbob.core import naming
from sbob.core.batch import Job, list_inputs, plan_jobs
from sbob.core.report import StepReport
from sbob.core.status import AUDIO_EXT
from sbob.steps.base import StepContext

# whisper: non mantenuto, lasciato per chi forka (vedi whisper_mlx.py)
BACKENDS = {"gemini": "gemini", "notebooklm": "notebooklm", "html": "import_html", "whisper": "whisper_mlx"}


def clean_stem(stem: str) -> str:
    """'2025-09-17_elettronica_lezione_01.aac' → senza estensione audio/video lasciata dagli export."""
    for ext in (".aac", ".m4a", ".mp3", ".mp4", ".wav", ".ogg", ".flac"):
        if stem.lower().endswith(ext):
            return stem[: -len(ext)]
    return stem


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("trascrivi")
    backend = ctx.options.get("backend") or ctx.course.trascrizione
    if backend not in BACKENDS:
        rep.error = f"backend '{backend}' sconosciuto ({', '.join(BACKENDS)})"
        return rep
    lay = ctx.layout

    if backend == "html":
        html_dir = Path(ctx.options.get("html_dir") or lay.base / "export_html").expanduser()
        inputs = list_inputs(html_dir, [".html", ".htm"])
        if not inputs:
            rep.notes.append(f"Nessun .html in {html_dir}")
        dst_for = lambda p: lay.trascrizioni / f"{clean_stem(p.stem)}.md"  # noqa: E731
    else:
        inputs = list_inputs(lay.audio, AUDIO_EXT)
        dst_for = lambda p: lay.trascrizioni / f"{p.stem}.md"  # noqa: E731

    todo, done = plan_jobs(inputs, dst_for, force=ctx.force, only=ctx.only)
    rep.skipped = [j.dst.stem for j in done]
    for j in todo:
        if naming.parse(j.dst.stem) is None:
            rep.notes.append(f"nome non canonico: {j.dst.stem} (verrà trattato come lezione senza metadati)")
    if ctx.dry_run:
        rep.done = [j.dst.stem for j in todo]
        return rep
    if not todo:
        return rep

    lay.ensure("trascrizioni")
    module = importlib.import_module(f"sbob.steps.transcribe.{BACKENDS[backend]}")
    module.transcribe_many(ctx, todo, rep)
    return rep
