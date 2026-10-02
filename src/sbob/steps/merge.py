"""Passo merge (facoltativo): unisce i file del corso in merge/ per NotebookLM o per un LLM.

Modi (--modo):
  split     file da al massimo 500k parole (limite per sorgente di NotebookLM) → <slug>_<da>_1.md, _2.md, ...
  monolite  un solo file con indice e una sezione per documento (formato `compose`, deterministico)
  tde       come monolite ma per i temi d'esame (file con 'tde' nel nome), ordinati per data

Sorgente (--da): appunti (solo *_appunti.md) · trascrizioni · materiale (materiale_md/, convertito da `sbob materiale`).
Il merge rigenera sempre l'output: è un derivato, non c'è nulla da "saltare".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sbob.core import frontmatter, naming
from sbob.core.batch import atomic_write_text, list_inputs, list_tree
from sbob.core.report import StepReport
from sbob.steps.base import StepContext

WORD_LIMIT = 500_000
TYPE_NAMES = {"lez": "lezione", "ese": "esercitazione", "lab": "laboratorio", "sem": "seminario", "tde": "tema d'esame"}
SHORT_NAMES = {"lez": "Lezione", "ese": "Esercitazione", "lab": "Laboratorio", "sem": "Seminario", "tde": "Tema d'esame"}
_PLURALS = {"lez": ("lezione", "lezioni"), "ese": ("esercitazione", "esercitazioni"), "lab": ("laboratorio", "laboratori"),
            "sem": ("seminario", "seminari"), "tde": ("tema d'esame", "temi d'esame")}
_HEADING = re.compile(r"^#{1,5}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DATE_IT = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def downgrade_headers(content: str) -> str:
    """# → ## ecc. per mantenere la gerarchia, senza toccare le righe dentro i blocchi di codice (dove # è un commento)."""
    out, fence = [], None
    for line in content.split("\n"):
        if m := _FENCE.match(line):
            fence = None if fence == m.group(1) else (fence or m.group(1))
        out.append("#" + line if fence is None and _HEADING.match(line) else line)
    return "\n".join(out)


def nest_headers(content: str, top: int = 3) -> str:
    """Sposta i titoli in modo che il più alto diventi di livello `top` (sotto la sezione `##` del documento),
    senza toccare i blocchi di codice. Max livello 6."""
    levels, fence = [], None
    lines = content.split("\n")
    for line in lines:
        if m := _FENCE.match(line):
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None and (h := re.match(r"^(#{1,6})\s", line)):
            levels.append(len(h.group(1)))
    if not levels:
        return content
    shift, out, fence = top - min(levels), [], None
    for line in lines:
        if m := _FENCE.match(line):
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None and (h := re.match(r"^(#{1,6})(\s.*)$", line)):
            line = "#" * min(6, max(1, len(h.group(1)) + shift)) + h.group(2)
        out.append(line)
    return "\n".join(out)


def word_count(text: str) -> int:
    return len(text.split())


def extract_date(name: str) -> str | None:
    if m := _DATE.search(name):
        return m.group(1)
    if m := _DATE_IT.search(name):
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    return None


def week_of(day: str | None, start: str | None) -> int | str:
    if not day or not start:
        return "?"
    delta = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    return 0 if delta < 0 else delta // 7 + 1      # 0 = prima dell'inizio del corso


def it_date(day: str | None) -> str | None:
    """2026-04-14 → 14/04/2026."""
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y") if day else None


@dataclass
class Doc:
    path: Path
    stem: str          # senza suffisso _appunti
    meta: dict
    body: str
    day: str | None

    @property
    def tipo(self) -> str | None:
        n = naming.parse(self.stem)
        if n:
            return n.tipo
        if self.meta.get("tipo") in TYPE_NAMES:
            return self.meta["tipo"]
        return "tde" if "tde" in self.stem.lower() else None

    @property
    def topic(self) -> str | None:
        """Argomento dal frontmatter, altrimenti il primo titolo del documento."""
        if t := self.meta.get("argomento"):
            return str(t).strip()
        if topics := self.meta.get("argomenti"):
            return ", ".join(topics[:4])
        for line in self.body.splitlines():
            if _HEADING.match(line):
                return line.lstrip("# ").strip()
        return None

    @property
    def heading(self) -> str:
        """`Lezione 01 · 14/04/2026 · Argomento`; per un tema d'esame `Esame del 20/01/2023`."""
        n = naming.parse(self.stem)
        when = it_date(self.day)
        if self.tipo == "tde" and not n:
            return f"Esame del {when}" if when else self.stem.replace("_", " ")
        base = f"{SHORT_NAMES[n.tipo]} {n.num:02d}" if n else self.stem.replace("_", " ")
        return " · ".join(x for x in (base, when, self.topic if n else None) if x)


def load_docs(files: list[Path]) -> list[Doc]:
    docs = []
    for p in files:
        meta, body = frontmatter.read(p)
        stem = p.stem.removesuffix(naming.NOTES_SUFFIX)
        n = naming.parse(stem)
        docs.append(Doc(p, stem, meta, body, n.data.isoformat() if n else extract_date(p.name)))
    return sorted(docs, key=lambda d: (d.day or "9999-99-99", d.stem))


def source_files(ctx: StepContext, da: str) -> tuple[list[Path], str]:
    lay = ctx.layout
    if da == "appunti":
        return [p for p in list_inputs(lay.appunti, [".md"]) if p.stem.endswith(naming.NOTES_SUFFIX)], "appunti"
    if da == "trascrizioni":
        return list_inputs(lay.trascrizioni, [".md"]), "trascrizioni"
    if da == "materiale":
        return list_tree(lay.materiale_md, [".md"]), "materiale"
    raise ValueError(f"--da deve essere appunti|trascrizioni|materiale (non '{da}')")


def build_split(docs: list[Doc], limit: int | None = None) -> list[str]:
    limit = limit or WORD_LIMIT     # letto a runtime
    parts, cur, words = [], [], 0
    for d in docs:
        block = f"# {d.stem}\n\n{d.body.strip()}\n\n"
        w = word_count(block)
        if words + w > limit and words > 0:
            parts.append("".join(cur))
            cur, words = [], 0
        cur.append(block)
        words += w
    if cur:
        parts.append("".join(cur))
    return parts


def describe(docs: list[Doc]) -> str:
    """`18 lezioni e 8 laboratori, dal 14/04/2026 al 04/06/2026.`"""
    counts: dict[str, int] = {}
    for d in docs:
        counts[d.tipo or ""] = counts.get(d.tipo or "", 0) + 1
    parts = [f"{n} {_PLURALS[t][n != 1]}" if t else f"{n} {'documento' if n == 1 else 'documenti'}"
             for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    what = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " e " + parts[-1]
    days = sorted(d.day for d in docs if d.day)
    span = f", dal {it_date(days[0])} al {it_date(days[-1])}" if days else ""
    return f"{what}{span}. Ogni sezione è un documento."


def compose(title: str, intro: str, docs: list[Doc], edizione: str | None = None) -> str:
    """Unico formato dei file uniti (merge e taccuino): titolo, indice, una sezione per documento.
    Deterministico: stesso input → stessi byte (niente data di generazione), così l'hash decide se ricaricare.
    Niente emoji, righe decorative, marcatori né istruzioni per l'AI: nelle sorgenti NotebookLM sarebbero contenuto."""
    out = [f"# {title}", "", intro, "", "## Indice", ""]
    out += [f"- {d.heading}" for d in docs]
    for d in docs:
        info = " · ".join(x for x in (f"Tipo: {TYPE_NAMES[d.tipo]}" if d.tipo else None,
                                      f"Edizione {edizione}" if edizione else None, f"File: {d.path.name}") if x)
        out += ["", f"## {d.heading}", info, "", nest_headers(d.body.strip())]
    return "\n".join(out) + "\n"


def course_title(ctx_course, what: str, teacher: str | None = None) -> str:
    extra = f"{ctx_course.anno_accademico}" + (f", prof. {teacher}" if teacher else "")
    return f"{ctx_course.nome} — {what} ({extra})"


def build_monolite(docs: list[Doc], ctx: StepContext) -> str:
    c = ctx.course
    return compose(course_title(c, "Appunti delle lezioni", c.docente), describe(docs), docs, c.anno_accademico)


def build_tde(docs: list[Doc], ctx: StepContext) -> str:
    c = ctx.course
    return compose(course_title(c, "Temi d'esame", c.docente),
                   f"{len(docs)} temi d'esame, dal più vecchio al più recente.", docs, c.anno_accademico)


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("merge")
    modo, da = ctx.options.get("modo", "monolite"), ctx.options.get("da", "appunti")
    if modo not in ("split", "monolite", "tde"):
        rep.error = f"--modo deve essere split|monolite|tde (non '{modo}')"
        return rep
    try:
        files, label = source_files(ctx, da)
    except ValueError as e:
        rep.error = str(e)
        return rep
    if modo == "tde":
        # nome con "tde", oppure (per il materiale) frontmatter tipo: tde assegnato da `sbob materiale`
        files = [p for p in files if "tde" in p.name.lower() or frontmatter.read(p)[0].get("tipo") == "tde"]
    if not files:
        rep.notes.append(f"Nessun file da unire ({da}{', con tde nel nome' if modo == 'tde' else ''}).")
        return rep

    docs = load_docs(files)
    slug = ctx.course.slug
    lay = ctx.layout
    if modo == "split":
        parts = build_split(docs)
        outputs = {f"{slug}_{label}_{i}.md": text for i, text in enumerate(parts, 1)}
        stale_glob = f"{slug}_{label}_[0-9]*.md"
    else:
        outputs = {f"{slug}_{label}_monolite.md" if modo == "monolite" else f"{slug}_tde.md":
                   build_monolite(docs, ctx) if modo == "monolite" else build_tde(docs, ctx)}
        stale_glob = None

    rep.notes.append(f"{len(docs)} file → {len(outputs)} output ({sum(word_count(t) for t in outputs.values())} parole)")
    if ctx.dry_run:
        rep.done = list(outputs)
        return rep
    lay.ensure("merge")
    if stale_glob:
        for old in lay.merge.glob(stale_glob):
            if old.name not in outputs:
                old.unlink()
    for name, text in outputs.items():
        atomic_write_text(lay.merge / name, text)
        rep.done.append(name)
        rep.outputs.append(str(lay.merge / name))
    return rep
