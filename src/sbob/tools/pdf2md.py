"""PDF → Markdown: il modello vede le pagine VERE, non testo estratto.

Due modalità, scelte in base al provider del ruolo `pdf` (sovrascrivibile con `pdf_modo` in [modelli.pdf]):
  pdf       Gemini / Anthropic: ricevono un sotto-PDF di N pagine e lo leggono nativamente (layout, formule, figure)
  immagini  provider OpenAI-compatibili con visione (Qwen-VL via DashScope/OpenRouter, …): ogni pagina come PNG

Resilienza:
  - il lavoro è diviso in blocchi di `pagine_per_blocco` pagine, ognuno con il suo checkpoint
  - un blocco fallito o troncato viene ritentato pagina per pagina; una pagina fallita NON scrive checkpoint
  - l'output si assembla solo se tutte le pagine sono coperte; altrimenti i checkpoint restano e il run dopo riprende
  - --force rifà tutto anche se l'output esiste già
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from sbob.core import frontmatter, prompts
from sbob.core.batch import atomic_write_text
from sbob.core.report import NeedsHuman
from sbob.llm.base import ErrorKind, FilePart, ImagePart, Message, TextPart
from sbob.llm.registry import Role

DEFAULT_PAGES_PER_BLOCK = 8
RENDER_DPI = 150
NATIVE_PDF_PROVIDERS = {"gemini", "anthropic"}
_USER_PROMPT = {
    "it": "Trascrivi in Markdown le pagine {first}–{last} (di {total}) del documento allegato, nell'ordine.",
    "en": "Transcribe pages {first}–{last} (of {total}) of the attached document into Markdown, in order.",
}


@dataclass
class PdfResult:
    pages: int = 0
    converted: int = 0                  # pagine elaborate in questo run
    resumed: int = 0                    # pagine già coperte dai checkpoint
    failed: dict[int, str] = field(default_factory=dict)   # pagina (1-based) → errore
    output: Path | None = None          # None se incompleto

    @property
    def complete(self) -> bool:
        return self.output is not None


def pdf_mode(role: Role) -> str:
    explicit = role.conf.get("pdf_modo")
    if explicit:
        return explicit
    return "pdf" if role.provider_conf.get("tipo") in NATIVE_PDF_PROVIDERS else "immagini"


def _ckpt_name(first: int, last: int) -> str:
    return f"pagine_{first:05d}-{last:05d}.md"   # 0-based, estremi inclusi


def _covered(ckpt: Path) -> set[int]:
    pages: set[int] = set()
    for p in ckpt.glob("pagine_*.md"):
        a, b = p.stem.removeprefix("pagine_").split("-")
        pages.update(range(int(a), int(b) + 1))
    return pages


def _blocks(todo: list[int], size: int) -> list[tuple[int, int]]:
    """Raggruppa pagine consecutive in blocchi da al massimo `size`."""
    out: list[tuple[int, int]] = []
    for n in todo:
        if out and n == out[-1][1] + 1 and n - out[-1][0] < size:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def _message(pdf: Path, first: int, last: int, total: int, mode: str, lingua: str, tmp: Path) -> Message:
    import pymupdf as fitz  # extra `pdf`

    text = _USER_PROMPT.get(lingua, _USER_PROMPT["it"]).format(first=first + 1, last=last + 1, total=total)
    with fitz.open(pdf) as doc:
        if mode == "pdf":
            sub = fitz.open()
            sub.insert_pdf(doc, from_page=first, to_page=last)
            part_path = tmp / f"{pdf.stem}_{first + 1}-{last + 1}.pdf"
            sub.save(part_path)
            sub.close()
            return Message.user(FilePart(part_path, "application/pdf"), TextPart(text))
        images = [ImagePart(doc.load_page(n).get_pixmap(dpi=RENDER_DPI).tobytes("png"), "image/png")
                  for n in range(first, last + 1)]
    return Message.user(*images, TextPart(text))


def convert_pdf(pdf: Path, out: Path, role: Role, lingua: str = "it", *, force: bool = False,
                checkpoint_dir: Path | None = None, meta: dict | None = None, workers: int | None = None,
                pages_per_block: int = DEFAULT_PAGES_PER_BLOCK,
                log: Callable[[str], None] = lambda m: None) -> PdfResult:
    import pymupdf as fitz

    res = PdfResult()
    if out.exists() and not force:
        res.output = out
        return res

    ckpt = checkpoint_dir or out.parent / ".checkpoints" / f"{pdf.stem}_checkpoints"
    if force and ckpt.exists():
        shutil.rmtree(ckpt)
    ckpt.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf) as doc:
        res.pages = len(doc)
    if res.pages == 0:
        raise ValueError(f"{pdf.name}: il PDF ha 0 pagine")

    done = _covered(ckpt)
    todo = [n for n in range(res.pages) if n not in done]
    res.resumed = res.pages - len(todo)
    mode, system = pdf_mode(role), prompts.load(lingua, "pdf")
    log(f"{pdf.name}: {res.pages} pagine ({res.resumed} già fatte), {role.label}, modalità {mode}")

    def call(first: int, last: int, tmp: Path):
        """None se ok, altrimenti (ErrorKind, messaggio)."""
        r = role.complete([_message(pdf, first, last, res.pages, mode, lingua, tmp)],
                          item=f"{pdf.stem}#p{first + 1}-{last + 1}", system=system,
                          validate=lambda t: None if t.strip() else "risposta vuota")
        if r.ok:
            atomic_write_text(ckpt / _ckpt_name(first, last), r.text.strip() + "\n")
            return None
        return r.error_kind, r.error or "errore"

    def block(first: int, last: int) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            err = call(first, last, tmp)
            if err is None:
                return
            kind, msg = err
            # pagina per pagina solo se il problema è il blocco (troppo lungo, risposta non valida): con il servizio
            # sovraccarico o giù (SERVER, RATE_LIMIT) dividere moltiplica le chiamate e brucia la quota → si riprova al run dopo
            if first == last or kind in (ErrorKind.SERVER, ErrorKind.RATE_LIMIT):
                for n in range(first, last + 1):
                    res.failed[n + 1] = msg
                return
            log(f"{pdf.name}: blocco {first + 1}–{last + 1} fallito ({msg}); ritento pagina per pagina")
            for n in range(first, last + 1):
                if (e := call(n, n, tmp)) is not None:
                    res.failed[n + 1] = e[1]

    with ThreadPoolExecutor(max_workers=workers or role.workers) as pool:
        futures = [pool.submit(block, a, b) for a, b in _blocks(todo, pages_per_block)]
        try:
            for f in futures:
                f.result()
        except NeedsHuman:              # quota esaurita ovunque: i checkpoint restano, si riprende al prossimo run
            for f in futures:
                f.cancel()
            raise
    res.converted = len(todo) - len(res.failed)

    if set(range(res.pages)) - _covered(ckpt):   # incompleto: niente output, checkpoint intatti
        return res
    parts = sorted(ckpt.glob("pagine_*.md"))
    body = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n"
    atomic_write_text(out, frontmatter.join(meta or {}, body))
    shutil.rmtree(ckpt, ignore_errors=True)
    res.output = out
    return res
