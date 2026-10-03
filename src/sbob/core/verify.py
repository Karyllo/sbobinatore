"""Controllo di qualità dei file di un corso, leggibile da un agente (`sbob verifica --json`).

Cerca i difetti che altrimenti si scoprono troppo tardi: appunti molto più corti della trascrizione (contenuto perso),
trascrizioni troncate rispetto alla durata dell'audio, loop di ripetizione, file parziali o con banner di errore,
buchi nella numerazione, nomi non canonici, schede della mappa mancanti o vecchie, e lezioni scritte in prevalenza
da un modello diverso dal principale (dai log: di solito una riserva a quota finita) o con l'intestazione `modello:` falsa.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from sbob.config import Course
from sbob.core import frontmatter, index, media, naming
from sbob.core.batch import list_inputs
from sbob.core.layout import Layout
from sbob.core.status import AUDIO_EXT, VIDEO_EXT
from sbob.core.text import collapse_repetitions

MIN_NOTES_RATIO = 0.6          # appunti / trascrizione (in parole): sotto → probabile perdita di contenuto
MIN_WORDS_PER_MINUTE = 50      # parlato tipico 100-150 parole/min: sotto → trascrizione troncata


def models_from_log(lay: Layout, role: str = "notes") -> dict[str, Counter[str]]:
    """{lezione: Counter(modello → blocchi)} dai log (`costs.jsonl`): per ogni blocco conta l'ULTIMA richiesta riuscita,
    cioè quella accettata. Le lezioni con blocchi mancanti nel log (ripresi dalla cache) non compaiono: non si sa."""
    last: dict[tuple[str, int], str] = {}
    try:
        lines = lay.costs.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = re.match(r"^(.*)#(\d+)$", str(d.get("item", "")))
        if m and d.get("role") == role and not d.get("error"):
            last[(m.group(1), int(m.group(2)))] = d.get("model", "?")
    by_stem: dict[str, dict[int, str]] = {}
    for (stem, i), model in last.items():
        by_stem.setdefault(stem, {})[i] = model
    return {stem: Counter(blocks.values()) for stem, blocks in by_stem.items()
            if sorted(blocks) == list(range(1, max(blocks) + 1))}


def _issue(level: str, lesson: str | None, problem: str, fix: str | None = None) -> dict[str, Any]:
    return {"livello": level, "lezione": lesson, "problema": problem, "suggerimento": fix}


def verify_course(course: Course, check_audio: bool = True, primary_notes: str | None = None) -> dict[str, Any]:
    """`primary_notes`: modello principale del ruolo appunti (da config). Se c'è, si segnalano le lezioni scritte in
    prevalenza da un altro modello (di solito una riserva, perché il principale aveva finito la quota)."""
    lay = Layout.of(course)
    issues: list[dict[str, Any]] = []
    slug = course.slug
    by_model = models_from_log(lay)
    off_primary: list[tuple[str, str, float]] = []

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
            nmeta, nbody = frontmatter.read(notes)
            nw = len(nbody.split())
            if counts := by_model.get(stem):
                from sbob.steps.notes.pipeline import format_models

                actual, declared = format_models(counts), str(nmeta.get("modello") or "")
                if declared and "da cache" not in declared and declared != actual:
                    issues.append(_issue("avviso", stem, f"l'intestazione dice «{declared}» ma i log dicono «{actual}»",
                                         "intestazione scritta prima del controllo sul modello effettivo: i log hanno ragione"))
                if primary_notes:
                    other = sum(n for m, n in counts.items() if m != primary_notes)
                    if other / sum(counts.values()) >= 0.5:
                        top = max(counts, key=lambda m: counts[m])
                        off_primary.append((stem, top, other / sum(counts.values())))
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

    if off_primary and primary_notes:
        models = ", ".join(sorted({m for _, m, _ in off_primary}))
        issues.append(_issue("avviso", None, f"{len(off_primary)} lezioni scritte per più della metà da {models} e non da "
                             f"{primary_notes} (il modello principale): una riserva perché la quota era finita, oppure una tua scelta con --notes",
                             "qualità possibile diversa; per rifarle col principale quando la quota torna: "
                             f"sbob appunti {slug} --solo <lezione> --force (guarda `sbob quota`)"))
    order = {"errore": 0, "avviso": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["livello"]], i["lezione"] or ""))
    return {"corso": slug, "problemi": issues,
            "totali": {k: sum(1 for i in issues if i["livello"] == k) for k in order}}
