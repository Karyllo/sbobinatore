"""Scelta dei corsi WeBeep da sincronizzare: `sbob webeep scegli`.

Dalla lista dei corsi a cui si è iscritti (decine) si scelgono quelli da seguire: per ognuno si crea il blocco in
sbob.toml (slug, cartella, webeep_id, fonti). I corsi tolti dalle spunte NON vengono cancellati: si scollegano
(si toglie webeep_id) e i file restano dove sono.
"""

from __future__ import annotations

import re
import sys

from sbob.config import Settings
from sbob.core import archivio as ar
from sbob.core import naming
from sbob.core.report import NeedsHuman, StepReport
from sbob.setup import q, remove_course_field

_CODE = re.compile(r"^\s*\d{4,6}\s*-\s*")
_TAGS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")


def webeep_courses() -> list[dict]:
    from sbob.auth.browser import load_token
    from sbob.webeep.client import WebeepClient

    return WebeepClient(load_token() or "").courses()


def interactive(as_json: bool) -> bool:
    return sys.stdin.isatty() and not as_json


def short_name(fullname: str) -> str:
    """'078046 - ANALISI MATEMATICA (2) (ROSSI MARIO) [2025-26]' → 'Analisi Matematica'."""
    name = _TAGS.sub("", _CODE.sub("", fullname))
    return re.sub(r"\s+", " ", name).strip(" -").title()


def render_block(slug: str, course: dict, anno: str) -> str:
    _, teacher = ar.parse_fullname(course["nome"])
    lines = [f"[corsi.{slug}]", f"nome = {q(short_name(course['nome']))}", f"anno_accademico = {q(anno)}",
             f"cartella = {q(f'{anno}/{slug}')}", f"webeep_id = {course['id']}",
             'sorgenti = [ { tipo = "archivio" }, { tipo = "txt", file = "link.txt" } ]', 'trascrizione = "gemini"']
    if teacher:
        lines.append(f"docente = {q(teacher.title())}")
    return "\n".join(lines) + "\n"


def _unique_slug(base: str, taken: set[str]) -> str:
    slug, i = base or "corso", 2
    while slug in taken:
        slug, i = f"{base}_{i}", i + 1
    return slug


def run_scegli(settings: Settings, ids: list[int] | None, tutti_gli_anni: bool, as_json: bool,
               unlink: bool | None = None) -> StepReport:
    rep = StepReport(step="scegli", corso="")
    if settings.path is None:
        rep.error = "Nessun sbob.toml: lancia prima `sbob init`."
        return rep
    try:
        courses = webeep_courses()
    except NeedsHuman as e:
        rep.needs_human, rep.action = str(e), e.action
        return rep
    years = sorted({c["anno"] for c in courses if c["anno"]})
    shown = courses if tutti_gli_anni else [c for c in courses if c["anno"] == (years[-1] if years else None)]
    linked = {c.webeep_id: slug for slug, c in settings.corsi.items() if c.webeep_id}

    if ids is not None:                                         # non interattivo: esattamente questi, senza scollegare
        picked = set(ids)
        unknown = picked - {c["id"] for c in courses}
        if unknown:
            rep.error = f"Corsi WeBeep non trovati tra i tuoi: {sorted(unknown)} (sbob webeep corsi)"
            return rep
    elif interactive(as_json):
        import questionary

        picked = set(questionary.checkbox(
            "Quali corsi vuoi sincronizzare? (spazio = spunta, invio = conferma)",
            choices=[questionary.Choice(f"{c['anno'] or '?'}  {short_name(c['nome'])}", value=c["id"],
                                        checked=c["id"] in linked) for c in shown]).ask() or set())
        if not picked and not linked:
            rep.notes.append("Nessun corso scelto.")
            return rep
    else:
        rep.error = "Serve un terminale per scegliere: da script usa `--id <id>` (ripetibile)."
        return rep

    taken = set(settings.corsi)
    new_blocks = []
    for c in shown if ids is None else [c for c in courses if c["id"] in picked]:
        if c["id"] not in picked or c["id"] in linked:
            continue
        anno = c["anno"] or "sconosciuto"
        slug = _unique_slug(naming.slugify(short_name(c["nome"])), taken)
        if interactive(as_json) and ids is None:
            import questionary

            slug = _unique_slug(naming.slugify(questionary.text(f"Nome breve per «{short_name(c['nome'])}» (nei file):",
                                                                default=slug).ask() or slug), taken)
        taken.add(slug)
        new_blocks.append(render_block(slug, c, anno))
        rep.done.append(f"{slug} ← WeBeep {c['id']}")
    if new_blocks:
        with open(settings.path, "a", encoding="utf-8") as f:
            f.write("".join("\n" + b for b in new_blocks))

    if ids is None:
        shown_ids = {c["id"] for c in shown}
        dropped = [(wid, slug) for wid, slug in linked.items() if wid in shown_ids and wid not in picked]
        for wid, slug in dropped:
            if unlink is None and interactive(as_json):
                import questionary

                unlink_it = questionary.confirm(f"Scollego «{slug}» da WeBeep? (i file restano dove sono)", default=False).ask()
            else:
                unlink_it = bool(unlink)
            if unlink_it and remove_course_field(settings.path, slug, "webeep_id"):
                rep.done.append(f"{slug} scollegato (i file restano)")
            else:
                rep.skipped.append(f"{slug} (tolto dalle spunte ma ancora collegato)")
    if not rep.done:
        rep.notes.append("Niente da cambiare.")
    else:
        rep.notes.append("Ora: sbob aggiorna --dry-run")
    return rep
