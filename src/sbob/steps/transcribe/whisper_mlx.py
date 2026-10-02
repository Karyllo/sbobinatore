"""Backend whisper: mlx-whisper locale su Apple Silicon (nessun dato esce dal Mac). Richiede l'extra `whisper`.

NON MANTENUTO: l'autore usa Gemini (default) o NotebookLM. Il backend resta per chi forka il progetto e vuole
una trascrizione locale; non riceve sviluppi né viene provato con dati reali.
"""

from __future__ import annotations

from sbob.core import media
from sbob.core.batch import Job
from sbob.core.report import NeedsHuman, StepReport
from sbob.core.text import collapse_repetitions, format_ts, paragraphs
from sbob.steps.base import StepContext
from sbob.steps.transcribe.base import write_transcript

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo-q4"


def transcribe_file(audio, modello: str, lingua: str) -> str:
    try:
        import mlx_whisper
    except ImportError:
        raise NeedsHuman("mlx-whisper non installato", action="uv sync --extra whisper") from None
    result = mlx_whisper.transcribe(
        str(audio), path_or_hf_repo=modello, language=lingua, verbose=False, word_timestamps=False,
        condition_on_previous_text=False,   # evita i loop di ripetizione
        compression_ratio_threshold=2.4,    # scarta segmenti ripetitivi
        no_speech_threshold=0.6,            # salta silenzio/rumore
    )
    segments = result.get("segments", [])
    if not segments:
        return paragraphs(collapse_repetitions(result.get("text", "")))
    # paragrafi di ~500 caratteri, ognuno con il tempo esatto del suo primo segmento
    out, buf, size, start = [], [], 0, None
    for s in segments:
        t = s.get("text", "").strip()
        if not t:
            continue
        start = s.get("start", 0.0) if start is None else start
        buf.append(t)
        size += len(t) + 1
        if size >= 500:
            out.append(f"{format_ts(start)} {collapse_repetitions(' '.join(buf))}")
            buf, size, start = [], 0, None
    if buf:
        out.append(f"{format_ts(start)} {collapse_repetitions(' '.join(buf))}")
    return "\n\n".join(out)


def transcribe_many(ctx: StepContext, jobs: list[Job], rep: StepReport) -> None:
    modello = ctx.options.get("modello") or ctx.settings.raw.get("whisper", {}).get("modello", DEFAULT_MODEL)
    for job in jobs:
        dur = media.duration_seconds(job.src)
        ctx.log(f"whisper: {job.src.name} ({dur / 60:.0f} min) con {modello}")
        try:
            text = transcribe_file(job.src, modello, ctx.course.lingua)
        except NeedsHuman:
            raise
        except Exception as e:  # noqa: BLE001
            rep.fail(job.dst.stem, e)
            continue
        write_transcript(ctx, job, text, rep, backend="whisper", model=modello)
