"""Backend html: importa gli export manuali (es. sorgenti scaricate da NotebookLM) in trascrizioni/. Richiede l'extra `html`."""

from __future__ import annotations

from sbob.core.batch import Job
from sbob.core.report import NeedsHuman, StepReport
from sbob.steps.base import StepContext
from sbob.steps.transcribe.base import write_transcript


def transcribe_many(ctx: StepContext, jobs: list[Job], rep: StepReport) -> None:
    try:
        from markdownify import markdownify
    except ImportError:
        raise NeedsHuman("markdownify non installato", action="uv sync --extra html") from None
    for job in jobs:
        try:
            text = markdownify(job.src.read_text(encoding="utf-8")).strip()
        except Exception as e:  # noqa: BLE001
            rep.fail(job.dst.stem, e)
            continue
        write_transcript(ctx, job, text, rep, backend="html-import")
