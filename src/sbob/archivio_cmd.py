"""Logica di `sbob archivio <corso> <azione>`: aggiungere edizioni passate, scegliere il docente, elencarle."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from sbob.config import ConfigError, Settings
from sbob.core import archivio as ar
from sbob.core.report import NeedsHuman, StepReport
from sbob.setup import set_course_field


def _webeep_courses() -> list[dict]:
    from sbob.auth.browser import load_token
    from sbob.webeep.client import WebeepClient

    return WebeepClient(load_token() or "").courses()


def _interactive(as_json: bool) -> bool:
    return sys.stdin.isatty() and not as_json


def run_archivio(settings: Settings, corso: str, azione: str, anno: str | None, docente: str | None,
                 forced_id: int | None, as_json: bool) -> StepReport:
    rep = StepReport(step="archivio", corso=corso)
    try:
        course = settings.corso(corso)
        if azione == "elenco":
            for a in ar.archives_of(course):
                rep.done.append(f"{a.anno_accademico} → WeBeep {a.webeep_id} ({a.cartella})")
            if not rep.done:
                rep.notes.append(f"Nessuna edizione passata. Aggiungi: sbob archivio {corso} aggiungi")
            return rep
        if azione == "docenti":
            return _docenti(settings, course, rep, as_json)
        if azione == "aggiungi":
            return _aggiungi(settings, course, rep, anno, docente, forced_id, as_json)
        rep.error = f"azione '{azione}' non valida (aggiungi | docenti | elenco)"
    except NeedsHuman as e:
        rep.needs_human, rep.action = str(e), e.action
    except (ConfigError, ValueError) as e:
        rep.error = str(e)
    return rep


def _docenti(settings: Settings, course, rep: StepReport, as_json: bool) -> StepReport:
    teachers = ar.teachers_for_code(course, _webeep_courses())
    current = course.archivio_docente or ar.current_teacher(course, _webeep_courses())[1]
    for t in teachers:
        rep.notes.append(f"{t['docente']}: {', '.join(sorted(set(t['anni'])))}")
    if not _interactive(as_json):
        rep.notes.append(f"Docente in uso: {current}. Per cambiarlo: sbob archivio {course.slug} docenti (da terminale).")
        return rep
    import questionary

    chosen = questionary.select(f"Quale docente? (in uso: {current})",
                                choices=[questionary.Choice(f"{t['docente']} ({', '.join(sorted(set(t['anni'])))})",
                                                            value=t["docente"]) for t in teachers]).ask()
    if chosen:
        set_course_field(cast(Path, settings.path), course.slug, "archivio_docente", chosen)
        rep.done.append(f"docente scelto: {chosen}")
        rep.notes.append(f"Ora: sbob archivio {course.slug} aggiungi")
    return rep


def _aggiungi(settings: Settings, course, rep: StepReport, anno: str | None, docente: str | None,
              forced_id: int | None, as_json: bool) -> StepReport:
    wb = _webeep_courses()
    base = replace(course, archivio_docente=docente) if docente else course
    cand = ar.candidates(base, wb)
    teacher = cand["docente"]
    added: dict[str, int] = {}
    problems: list[str] = []

    if forced_id is not None:                                    # --id: nessun controllo sul docente
        target = next((c for c in wb if c["id"] == forced_id), None)
        if target is None or not (anno or target["anno"]):
            raise ValueError(f"Corso WeBeep {forced_id} non trovato tra i tuoi corsi, o senza anno (indica l'anno)")
        added[anno or target["anno"]] = forced_id
    else:
        if anno:
            years = [anno]
        else:
            years = sorted((y for y in cand["trovate"] if y not in course.archivio), reverse=True)
            if years and _interactive(as_json):
                import questionary
                years = questionary.checkbox("Quali edizioni aggiungo?", choices=years).ask() or []
        for y in years:
            found = cand["trovate"].get(y)
            if y >= course.anno_accademico:
                problems.append(f"{y}: non è un anno precedente a {course.anno_accademico}")
            elif not found:
                others = cand["altri_docenti"].get(y, [])
                names = ", ".join(sorted({o["docente"] for o in others if o.get("docente")})) or "nessuna edizione"
                problems.append(f"{y}: nessuna edizione con lo stesso docente ({teacher}). Altri docenti per quel codice: {names}")
            elif len(found) > 1:
                if _interactive(as_json):
                    import questionary
                    pick = questionary.select(f"Più edizioni nel {y}:", choices=[questionary.Choice(
                        f"{f['id']} · {f['nome'][:70]}", value=f["id"]) for f in found]).ask()
                    if pick:
                        added[y] = pick
                else:
                    problems.append(f"{y}: più edizioni ({', '.join(str(f['id']) for f in found)}): scegli con --id")
            else:
                added[y] = found[0]["id"]

    if added:
        set_course_field(cast(Path, settings.path), course.slug, "archivio", {**course.archivio, **added})
        rep.done = [f"{y} → WeBeep {i}" for y, i in sorted(added.items(), reverse=True)]
        rep.notes.append(f"Ora: sbob materiale {course.slug} --archivio <anno>  ·  sbob run {course.slug} --archivio <anno>")
    if problems and not added:
        raise NeedsHuman("; ".join(problems),
                         action=f"sbob archivio {course.slug} docenti  (scegli il docente)  oppure  "
                                f"sbob archivio {course.slug} aggiungi <anno> --id <id WeBeep>")
    rep.warnings += problems
    if not added and not problems:
        rep.notes.append(f"Nessuna edizione passata con lo stesso docente ({teacher}) per il codice {cand['codice']}. "
                         f"Se è cambiato il docente: sbob archivio {course.slug} docenti")
    return rep
