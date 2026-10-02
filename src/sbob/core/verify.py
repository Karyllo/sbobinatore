"""Controllo di qualità dei file di un corso, leggibile da un agente (`sbob verifica --json`).

Cerca i difetti che altrimenti si scoprono troppo tardi: appunti molto più corti della trascrizione (contenuto perso),
trascrizioni troncate rispetto alla durata dell'audio, loop di ripetizione, file parziali o con banner di errore,
buchi nella numerazione, nomi non canonici, schede della mappa mancanti o vecchie.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sbob.config import Course
from sbob.core import frontmatter, index, media, naming
from sbob.core.batch import list_inputs
from sbob.core.layout import Layout
from sbob.core.status import AUDIO_EXT, VIDEO_EXT
from sbob.core.text import collapse_repetitions

MIN_NOTES_RATIO = 0.6          # appunti / trascrizione (in parole): sotto → probabile perdita di contenuto
MIN_WORDS_PER_MINUTE = 50      # parlato tipico 100-150 parole/min: sotto → trascrizione troncata


def _issue(level: str, lesson: str | None, problem: str, fix: str | None = None) -> dict[str, Any]:
    return {"livello": level, "lezione": lesson, "problema": problem, "suggerimento": fix}


def verify_course(course: Course, check_audio: bool = True) -> dict[str, Any]:
    lay = Layout.of(course)
    issues: list[dict[str, Any]] = []
    slug = course.slug

    # nomi e numerazione
    stems = {p.stem for d, ext in ((lay.video, VIDEO_EXT), (lay.audio, AUDIO_EXT), (lay.trascrizioni, [".md"]))
             for p in list_inputs(d, ext)}
    stems |= {p.stem.removesuffix(naming.NOTES_SUFFIX) for p in list_inputs(lay.appunti, [".md"])
              if p.stem.endswith(naming.NOTES_SUFFIX)}
    by_type: dict[str, set[int]] = {}
    for s in sorted(stems):
        n = naming.parse(s)
        if n is None:
            issues.append(_issue("avviso", s, "nome non canonico (YYYY-MM-DD_corso_lezNN)",
                                 "rinomina il file: senza nome canonico mancano data e numero nella mappa"))
        else:
            by_type.setdefault(n.tipo, set()).add(n.num)
    for tipo, nums in by_type.items():
        if missing := sorted(set(range(1, max(nums) + 1)) - nums):
            issues.append(_issue("avviso", None, f"numerazione {tipo}: mancano {', '.join(f'{tipo}{m:02d}' for m in missing)}",
                                 "registrazione non scaricata o numerata male?"))

    # parziali
    for p in lay.appunti.glob("*.parziale.md") if lay.appunti.exists() else []:
        issues.append(_issue("errore", p.name.removesuffix(".parziale.md"), "appunti parziali (alcuni blocchi falliti)",
                             f"sbob appunti {slug} --solo {p.name.removesuffix('.parziale.md')}"))

    schede = index.load_schede(course)
    for tr in list_inputs(lay.trascrizioni, [".md"]):
        stem = tr.stem
        _, tbody = frontmatter.read(tr)
        tw = len(tbody.split())
        if tw == 0:
            issues.append(_issue("errore", stem, "trascrizione vuota", f"sbob trascrivi {slug} --solo {stem} --force"))
            continue
        collapsed = len(collapse_repetitions(tbody).split())
        if (tw - collapsed) / tw > 0.03:
            issues.append(_issue("avviso", stem, f"loop di ripetizione nella trascrizione ({tw - collapsed} parole ripetute)",
                                 "ritrascrivi con un altro backend (--backend gemini)"))
        if check_audio:
            audio = next((p for p in list_inputs(lay.audio, AUDIO_EXT) if p.stem == stem), None)
            if audio and (minutes := media.duration_seconds(audio) / 60) > 5 and tw / minutes < MIN_WORDS_PER_MINUTE:
                issues.append(_issue("errore", stem, f"trascrizione probabilmente troncata: {tw} parole per "
                                     f"{minutes:.0f} min di audio ({tw / minutes:.0f} parole/min)",
                                     f"sbob trascrivi {slug} --solo {stem} --force"))
        notes = lay.appunti / f"{stem}{naming.NOTES_SUFFIX}.md"
        if notes.exists():
            _, nbody = frontmatter.read(notes)
            nw = len(nbody.split())
            if "⚠️ ERRORE" in nbody:
                issues.append(_issue("errore", stem, "gli appunti contengono blocchi in errore",
                                     f"sbob appunti {slug} --solo {stem} --force"))
            blocks = nbody.split("\n---\n")
            if len(blocks) > 1:
                short = [i + 1 for i, b in enumerate(blocks) if len(b.split()) < 0.25 * nw / len(blocks)]
                if short:
                    issues.append(_issue("errore", stem, f"blocchi quasi vuoti negli appunti: {short} di {len(blocks)}",
                                         f"contenuto perso: sbob appunti {slug} --solo {stem} --force"))
            if nw / tw < MIN_NOTES_RATIO:
                issues.append(_issue("errore" if nw / tw < 0.3 else "avviso", stem,
                                     f"appunti molto più corti della trascrizione ({nw} vs {tw} parole, {nw / tw:.0%})",
                                     f"possibile contenuto perso: sbob appunti {slug} --solo {stem} --force"))
            h = hashlib.sha256(nbody.encode()).hexdigest()[:16]
            card = schede.get(stem)
            if card is None:
                issues.append(_issue("info", stem, "manca la scheda nella mappa", f"sbob mappa {slug}"))
            elif card.get("hash") != h:
                issues.append(_issue("info", stem, "scheda della mappa non aggiornata (appunti cambiati)", f"sbob mappa {slug}"))

    order = {"errore": 0, "avviso": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["livello"]], i["lezione"] or ""))
    return {"corso": slug, "problemi": issues,
            "totali": {k: sum(1 for i in issues if i["livello"] == k) for k in order}}
