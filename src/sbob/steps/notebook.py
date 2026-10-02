"""Passo notebook: tiene aggiornato un taccuino NotebookLM per corso, con gli appunti e il materiale.

Sorgenti (una per cartella, così quando cambia qualcosa si sostituisce solo quella):
  - `Appunti`: tutti gli appunti in ordine di data (se superano 500k parole: `Appunti (1)`, `Appunti (2)`, ...)
  - una per ogni cartella di primo livello sotto la sezione di materiale_md/ (es. `Materiali — esempi di temi d'esame`)
Le trascrizioni non si caricano (sarebbero ridondanti). Le edizioni passate solo con `sbob notebook <corso> aggiungi-archivio`.

Si toccano solo le sorgenti create da sbob (gli id stanno nel manifest): quelle aggiunte a mano restano.
Una sorgente cambiata si sostituisce così: aggiungi la nuova → attendi che sia pronta → cancella la vecchia.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sbob import notebooklm_cli as nlm
from sbob.core import prompts
from sbob.core.archivio import archive_course
from sbob.core.batch import atomic_write_text, list_inputs, list_tree
from sbob.core.report import StepReport
from sbob.steps import merge
from sbob.steps.base import StepContext

DEFAULT_MAX_SOURCES = 50
_UNSAFE = re.compile(r"[^\w\-— ().,']+")


def settings_of(ctx: StepContext) -> dict:
    return ctx.settings.raw.get("notebook", {})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunks(docs: list[merge.Doc], limit: int) -> list[list[merge.Doc]]:
    out: list[list[merge.Doc]] = [[]]
    words = 0
    for d in docs:
        w = merge.word_count(d.body)
        if words + w > limit and out[-1]:
            out.append([])
            words = 0
        out[-1].append(d)
        words += w
    return out


def _compose_group(course, label: str, docs: list[merge.Doc], prefix: str, edizione: str | None) -> dict[str, str]:
    limit = merge.WORD_LIMIT
    parts = _chunks(docs, limit)
    out = {}
    for i, part in enumerate(parts, 1):
        name = f"{prefix}{label}" + (f" ({i})" if len(parts) > 1 else "")
        what = label + (f" ({i}/{len(parts)})" if len(parts) > 1 else "")
        out[name] = merge.compose(merge.course_title(course, what, course.docente), merge.describe(part), part, edizione)
    return out


def plan_sources(course, prefix: str = "", edizione: str | None = None) -> dict[str, str]:
    """{titolo sorgente: testo}. Vuoto se non c'è ancora niente."""
    from sbob.core.layout import Layout

    lay = Layout.of(course)
    wanted: dict[str, str] = {}
    notes = [p for p in list_inputs(lay.appunti, [".md"]) if p.stem.endswith("_appunti")]
    if notes:
        wanted |= _compose_group(course, "Appunti", merge.load_docs(notes), prefix, edizione)

    groups: dict[str, list[Path]] = {}
    for p in list_tree(lay.materiale_md, [".md"]):
        parts = p.relative_to(lay.materiale_md).parts
        # sezione/cartella/... → "sezione — cartella"; sezione/file → "sezione"; file alla radice → "Materiale"
        key = f"{parts[0]} — {parts[1]}" if len(parts) >= 3 else (parts[0] if len(parts) == 2 else "Materiale")
        groups.setdefault(key, []).append(p)
    for key, files in sorted(groups.items()):
        wanted |= _compose_group(course, key, merge.load_docs(files), prefix, edizione)
    return wanted


def wanted_sources(ctx: StepContext, state: dict) -> dict[str, str]:
    wanted = plan_sources(ctx.course, edizione=ctx.course.anno_accademico)
    for anno in state.get("archivi", []):
        if anno in ctx.course.archivio:
            wanted |= plan_sources(archive_course(ctx.course, anno), prefix=f"Edizione {anno} — ", edizione=anno)
    return wanted


def _file_for(ctx: StepContext, title: str, text: str) -> Path:
    path = ctx.layout.notebook / f"{_UNSAFE.sub(' ', title).strip()}.md"
    atomic_write_text(path, text)
    return path.resolve()           # il CLI rifiuta i symlink (su macOS anche /tmp)


def sync(ctx: StepContext, rep: StepReport, nb_id: str, wanted: dict[str, str], known: dict[str, dict]) -> None:
    """Allinea il taccuino a `wanted`. `known` = sorgenti caricate da sbob ({titolo: {hash, id}}), modificato sul posto."""
    on_server = {s.get("id") for s in nlm.list_sources(nb_id)}
    count = len(on_server)
    limit = int(settings_of(ctx).get("max_sorgenti", DEFAULT_MAX_SOURCES))

    for title, text in wanted.items():
        h = _sha(text)
        old = known.get(title)
        if old and old["hash"] == h and old["id"] in on_server and not ctx.force:
            rep.skipped.append(title)
            continue
        replace = bool(old and old["id"] in on_server)
        if not replace and count >= limit:
            rep.warnings.append(f"{title}: non aggiunta, il taccuino ha già {count} sorgenti (limite {limit}, [notebook] max_sorgenti)")
            continue
        if ctx.dry_run:
            rep.done.append(f"{'sostituirei' if replace else 'aggiungerei'}: {title}")
            continue
        try:
            sid = nlm.add_file(nb_id, str(_file_for(ctx, title, text)), title)
            try:
                nlm.wait_source(nb_id, sid)
            except RuntimeError:
                nlm.delete_source(nb_id, sid)           # mai lasciare una sorgente a metà
                raise
        except RuntimeError as e:
            rep.fail(title, str(e))
            continue
        if replace and old:
            try:
                nlm.delete_source(nb_id, old["id"])
            except RuntimeError as e:
                rep.warnings.append(f"{title}: vecchia versione non rimossa ({e})")
        else:
            count += 1
        known[title] = {"hash": h, "id": sid}
        rep.done.append(f"{'sostituita' if replace else 'aggiunta'}: {title}")

    for title in [t for t in known if t not in wanted]:     # sparite in locale (o archivio tolto)
        if ctx.dry_run:
            rep.done.append(f"rimuoverei: {title}")
            continue
        try:
            if known[title]["id"] in on_server:
                nlm.delete_source(nb_id, known[title]["id"])
            del known[title]
            (ctx.layout.notebook / f"{_UNSAFE.sub(' ', title).strip()}.md").unlink(missing_ok=True)   # file generato da noi
            rep.done.append(f"rimossa: {title}")
        except RuntimeError as e:
            rep.fail(title, str(e))


def _apply_persona(ctx: StepContext, rep: StepReport, nb_id: str, state: dict) -> None:
    text = prompts.load(ctx.course.lingua, "notebook_persona").strip()
    h = _sha(text)
    if state.get("persona") == h and not ctx.force:
        return
    if ctx.dry_run:
        rep.done.append("imposterei le istruzioni della chat")
        return
    try:
        nlm.set_persona(nb_id, text)
        state["persona"] = h
        rep.done.append("istruzioni della chat impostate")
    except RuntimeError as e:
        rep.warnings.append(f"istruzioni della chat non impostate: {e}")


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("notebook")
    action, anno = ctx.options.get("azione"), ctx.options.get("anno")
    explicit = action is not None or ctx.options.get("esplicito")
    if not explicit and not settings_of(ctx).get("attivo", False):
        rep.notes.append("Taccuino NotebookLM non attivo (in sbob.toml: [notebook] attivo = true, oppure `sbob notebook <corso>`).")
        return rep

    man = ctx.manifest()
    state = man.notebook
    if action in ("aggiungi-archivio", "rimuovi-archivio"):
        if not anno or (action == "aggiungi-archivio" and anno not in ctx.course.archivio):
            rep.error = (f"Indica un anno: tra {', '.join(sorted(ctx.course.archivio)) or '(nessuna edizione: sbob archivio ... aggiungi)'}"
                         if action == "aggiungi-archivio" else "Indica l'anno da togliere.")
            return rep
        years = set(state.get("archivi", []))
        years = years | {anno} if action == "aggiungi-archivio" else years - {anno}
        state["archivi"] = sorted(years)

    wanted = wanted_sources(ctx, state)
    if not wanted:
        rep.notes.append("Niente da caricare: mancano appunti e materiale convertito.")
        return rep

    if ctx.dry_run and not state.get("id"):
        rep.done.append("creerei il taccuino")
        rep.done += [f"aggiungerei: {t}" for t in wanted]
        return rep

    title = ctx.course.notebook_studio or f"{ctx.course.nome} ({ctx.course.anno_accademico})"
    if not state.get("id"):
        state["id"] = nlm.create_notebook(title)
        state.pop("persona", None)
        rep.done.append(f"taccuino creato: {title}")
    nb_id = state["id"]
    ctx.log(f"notebook: '{title}' ({nb_id}), {len(wanted)} sorgenti")
    known = state.setdefault("sources", {})
    _apply_persona(ctx, rep, nb_id, state)
    sync(ctx, rep, nb_id, wanted, known)
    if not ctx.dry_run:
        man.save()
    return rep

