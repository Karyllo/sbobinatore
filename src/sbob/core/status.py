"""Stato di un corso dedotto dai file: per ogni lezione, quali passi sono fatti."""

from __future__ import annotations

from typing import Any

from sbob.config import Course
from sbob.core import naming
from sbob.core.batch import list_inputs
from sbob.core.layout import Layout

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv")
AUDIO_EXT = (".aac", ".m4a", ".mp3", ".wav", ".ogg", ".flac", ".webm", ".aiff", ".qta")


def course_status(course: Course) -> dict[str, Any]:
    lay = Layout.of(course)
    stems: dict[str, dict[str, bool]] = {}

    def mark(step: str, files, strip_suffix: str = "") -> None:
        for p in files:
            stem = p.stem.removesuffix(strip_suffix) if strip_suffix else p.stem
            stems.setdefault(stem, {})[step] = True

    mark("video", list_inputs(lay.video, VIDEO_EXT))
    mark("audio", list_inputs(lay.audio, AUDIO_EXT))
    mark("trascrizione", list_inputs(lay.trascrizioni, [".md"]))
    mark("appunti", [p for p in list_inputs(lay.appunti, [".md"]) if p.stem.endswith(naming.NOTES_SUFFIX)],
         naming.NOTES_SUFFIX)

    steps = ("video", "audio", "trascrizione", "appunti")
    lezioni = []
    for stem in sorted(stems):  # gli stem canonici iniziano con la data → ordine cronologico
        done = stems[stem]
        flags = {s: done.get(s, False) for s in steps}
        prossimo = next((s for s in steps if not flags[s] and any(flags[t] for t in steps[:steps.index(s)])), None)
        lezioni.append({"lezione": stem, "nome_valido": naming.parse(stem) is not None,
                        **flags, "prossimo_passo": prossimo})

    totals = {s: sum(1 for l in lezioni if l[s]) for s in steps}
    return {
        "corso": course.slug,
        "nome": course.nome,
        "cartella": str(course.cartella),
        "esiste": course.cartella.exists(),
        "lezioni": lezioni,
        "totali": {"lezioni": len(lezioni), **totals},
        "da_fare": {
            "audio": [l["lezione"] for l in lezioni if l["video"] and not l["audio"]],
            "trascrizione": [l["lezione"] for l in lezioni if l["audio"] and not l["trascrizione"]],
            "appunti": [l["lezione"] for l in lezioni if l["trascrizione"] and not l["appunti"]],
        },
    }
