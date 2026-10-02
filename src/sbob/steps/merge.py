"""Passo merge (facoltativo): unisce i file del corso in merge/ per NotebookLM o per un LLM.

Modi (--modo):
  split     file da al massimo 500k parole (limite per sorgente di NotebookLM) → <slug>_<da>_1.md, _2.md, ...
  monolite  un solo file con indice, marker [[ID_SESSIONE_n]] e box di metadati per ogni lezione
  tde       come monolite ma per i temi d'esame (file con 'tde' nel nome), ordinati per data

Sorgente (--da): appunti (solo *_appunti.md) · trascrizioni · materiale (.sbob/md, vedi fase materiale).
Il merge rigenera sempre l'output: è un derivato, non c'è nulla da "saltare".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sbob.core import frontmatter, naming
from sbob.core.batch import atomic_write_text, list_inputs
from sbob.core.report import StepReport
from sbob.steps.base import StepContext

WORD_LIMIT = 500_000
TYPE_NAMES = {"lez": "🧠 Lezione Teorica", "ese": "✍️ Esercitazione Pratica",
              "lab": "🧪 Laboratorio / Sperimentale", "sem": "🎤 Seminario / Extra", "tde": "💀 Tema d'esame"}
SHORT_NAMES = {"lez": "Lezione", "ese": "Esercitazione", "lab": "Laboratorio", "sem": "Seminario", "tde": "Tema d'esame"}
_HEADING = re.compile(r"^#{1,5}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DATE_IT = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
RULE = "━" * 60


def downgrade_headers(content: str) -> str:
    """# → ## ecc. per mantenere la gerarchia, senza toccare le righe dentro i blocchi di codice (dove # è un commento)."""
    out, fence = [], None
    for line in content.split("\n"):
        if m := _FENCE.match(line):
            fence = None if fence == m.group(1) else (fence or m.group(1))
        out.append("#" + line if fence is None and _HEADING.match(line) else line)
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


@dataclass
class Doc:
    path: Path
    stem: str          # senza suffisso _appunti
    meta: dict
    body: str
    day: str | None

    @property
    def title(self) -> str:
        n = naming.parse(self.stem)
        base = f"{SHORT_NAMES[n.tipo]} {n.num:02d}" if n else self.stem.replace("_", " ").title()
        topics = self.meta.get("argomenti")
        return f"{base} — {', '.join(topics[:4])}" if topics else base


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
        return list_inputs(lay.state / "md", [".md"]), "materiale"
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


def build_monolite(docs: list[Doc], ctx: StepContext) -> str:
    c = ctx.course
    header = [f"# 🎓 {c.nome} — archivio unificato ({c.anno_accademico})",
              f"**Ultimo aggiornamento:** {date.today().strftime('%d/%m/%Y')}", "",
              "> **ISTRUZIONI PER L'AI:**",
              "> Questo documento contiene sessioni di diversi tipi:",
              "> - **Lezione Teorica:** definizioni, teoremi e concetti fondamentali.",
              "> - **Esercitazione:** applicazione pratica, risoluzione di problemi, trucchi di calcolo.",
              "> - **Laboratorio:** esperimenti, software, analisi di dati reali.",
              "> ", "> Quando rispondi, **specifica sempre** se l'informazione viene dalla teoria o dalla pratica.",
              "", "---"]
    toc, blocks = [], []
    for i, d in enumerate(docs, 1):
        n = naming.parse(d.stem)
        tipo = TYPE_NAMES.get(n.tipo if n else "", "📝 Nota")
        week = week_of(d.day, c.inizio_corso)
        toc.append(f"{i}. **{c.nome}** - {d.title} ({tipo})")
        blocks.append("\n".join([
            "\n" + RULE, f"## {i}. [{c.nome}] {d.title}", f"[[ID_SESSIONE_{i}]]",
            f"> 📚 **Materia:** {c.nome}", f"> 🏷️ **Tipo:** {tipo}",
            f"> 📅 **Data:** {d.day or 'Data sconosciuta'} (Settimana {week})",
            f"> 📂 **File:** `{d.path.name}`", RULE + "\n",
            downgrade_headers(d.body.strip()), f"\n[[FINE_SESSIONE_{i}]]\n<br>\n"]))
    return "\n".join([*header, "## 📑 INDICE GENERALE", "\n".join(toc), "\n---\n", *blocks]) + "\n"


def build_tde(docs: list[Doc], ctx: StepContext) -> str:
    header = [f"# 💀 {ctx.course.nome} — archivio temi d'esame (TDE)",
              f"**Generato il:** {date.today().isoformat()}", "",
              "### 🤖 ISTRUZIONI PER L'INTELLIGENZA ARTIFICIALE",
              "> Questo documento contiene la raccolta storica degli esami, divisi per data.",
              "> Cerca pattern ricorrenti negli esercizi tra le varie date.", "", "---"]
    toc, blocks, seen = [], [], {}
    for i, d in enumerate(docs, 1):
        day = d.day or "Data sconosciuta"
        key = day.replace("-", "_").replace(" ", "_")
        seen[key] = seen.get(key, 0) + 1
        ident = key if seen[key] == 1 else f"{key}_{seen[key]}"     # due esami nella stessa data → id distinti
        title = d.stem.replace("_", " ").replace("-", " ").title()
        toc.append(f"{i}. **{day}** - {title}")
        blocks.append("\n".join([
            "\n" + "━" * 50, f"## 🎓 {i}. ESAME DEL {day}", f"[[ID_TDE_{ident}]]", f"> 📅 **Data:** {day}",
            f"> 📂 **File:** `{d.path.name}`", "━" * 50 + "\n", downgrade_headers(d.body.strip()),
            f"\n[[FINE_TDE_{ident}]]\n<br>\n"]))
    return "\n".join([*header, "## 🗂️ INDICE CRONOLOGICO", "\n".join(toc), "\n---\n", *blocks]) + "\n"


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
        files = [p for p in files if "tde" in p.name.lower()]
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
