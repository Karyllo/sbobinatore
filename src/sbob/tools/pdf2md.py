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
import threading
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
    degraded: list[int] = field(default_factory=list)      # pagine (1-based) convertite solo con il testo (niente figure)
    output: Path | None = None          # None se incompleto

    @property
    def complete(self) -> bool:
        return self.output is not None


def pdf_mode(role: Role) -> str:
    explicit = role.conf.get("pdf_modo")
    if explicit:
        return explicit
    return "pdf" if role.provider_conf.get("tipo") in NATIVE_PDF_PROVIDERS else "immagini"


def _ckpt_name(first: int, last: int, text: bool = False) -> str:
    return f"pagine_{first:05d}-{last:05d}{'.testo' if text else ''}.md"   # 0-based, estremi inclusi


def _ckpt_map(ckpt: Path, text: bool) -> dict[int, Path]:
    """pagina (0-based) → file di checkpoint che la copre. text=True: solo la modalità 'solo testo'."""
    out: dict[int, Path] = {}
    for p in sorted(ckpt.glob("pagine_*.md")):
        stem = p.stem
        is_text = stem.endswith(".testo")
        if is_text != text:
            continue
        a, b = stem.removesuffix(".testo").removeprefix("pagine_").split("-")
        out.update({n: p for n in range(int(a), int(b) + 1)})
    return out


def _covered(ckpt: Path) -> set[int]:
    """Pagine già convertite con la VISIONE (quelle in sola modalità testo non contano: si rifanno quando c'è quota)."""
    return set(_ckpt_map(ckpt, text=False))


def _blocks(todo: list[int], size: int) -> list[tuple[int, int]]:
    """Raggruppa pagine consecutive in blocchi da al massimo `size`."""
    out: list[tuple[int, int]] = []
    for n in todo:
        if out and n == out[-1][1] + 1 and n - out[-1][0] < size:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


MIN_TEXT_CHARS = 10


def _pages_with_text(pdf: Path, pages: list[int]) -> set[int]:
    """Pagine con un testo estraibile: nelle scansioni (appunti a mano) non c'è niente da dare a un modello solo-testo."""
    import pymupdf as fitz

    with fitz.open(pdf) as doc:
        return {n for n in pages if len(doc.load_page(n).get_text("text").strip()) >= MIN_TEXT_CHARS}


def _text_message(pdf: Path, first: int, last: int, lingua: str) -> Message:
    """Modalità 'solo testo': testo estratto da ogni pagina, con un segnale dove ci sono figure non leggibili."""
    import pymupdf as fitz

    hint = {"it": "[SEGNALE: la pagina contiene figure o disegni che qui non sono visibili]",
            "en": "[SIGNAL: the page contains figures or drawings that are not visible here]"}.get(lingua, "")
    parts = []
    with fitz.open(pdf) as doc:
        for n in range(first, last + 1):
            page = doc.load_page(n)
            has_fig = bool(page.get_images()) or len(page.get_drawings()) >= 10
            parts.append(f"=== PAGINA {n + 1} ===\n{page.get_text('text', sort=True)}\n{hint if has_fig else ''}")
    return Message.user("\n".join(parts))


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


def text_fallback_role(settings, registry):
    """Ruolo `pdf_testo` per la modalità 'solo testo', o None se disattivata (`[materiale] se_finisce_quota = "ferma"`)
    o se manca la chiave del suo provider."""
    if (settings.raw.get("materiale", {}) or {}).get("se_finisce_quota", "testo") == "ferma":
        return None
    try:
        return registry.role("pdf_testo")
    except NeedsHuman:
        return None


def convert_pdf(pdf: Path, out: Path, role: Role, lingua: str = "it", *, force: bool = False,
                checkpoint_dir: Path | None = None, meta: dict | None = None, workers: int | None = None,
                pages_per_block: int = DEFAULT_PAGES_PER_BLOCK, text_role: Role | None = None,
                upgrade: bool = False, log: Callable[[str], None] = lambda m: None) -> PdfResult:
    """`text_role`: se la quota del modello a visione finisce, i blocchi rimasti passano a questo modello (solo testo).
    `upgrade`: rifà con la visione le pagine che erano state convertite in sola modalità testo."""
    import pymupdf as fitz

    res = PdfResult()
    if out.exists() and not force and not upgrade:
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
    out_of_quota = threading.Event()

    def call(first: int, last: int, tmp: Path):
        """None se ok, altrimenti (ErrorKind, messaggio)."""
        r = role.complete([_message(pdf, first, last, res.pages, mode, lingua, tmp)],
                          item=f"{pdf.stem}#p{first + 1}-{last + 1}", system=system,
                          validate=lambda t: None if t.strip() else "risposta vuota")
        if r.ok:
            atomic_write_text(ckpt / _ckpt_name(first, last), (r.text or "").strip() + "\n")
            return None
        return r.error_kind, r.error or "errore"

    def text_block(first: int, last: int) -> None:
        assert text_role is not None
        text_done = _ckpt_map(ckpt, text=True)
        wanted = [n for n in range(first, last + 1) if n not in text_done]
        readable = _pages_with_text(pdf, wanted)
        pages = [n for n in wanted if n in readable]      # le scansioni restano in attesa di un modello a visione
        for a, b in _blocks(pages, pages_per_block):
            r = text_role.complete([_text_message(pdf, a, b, lingua)], item=f"{pdf.stem}#t{a + 1}-{b + 1}",
                                   system=prompts.load(lingua, "pdf_testo"),
                                   validate=lambda t: None if t.strip() else "risposta vuota")
            if r.ok:
                atomic_write_text(ckpt / _ckpt_name(a, b, text=True), (r.text or "").strip() + "\n")
            else:
                for n in range(a, b + 1):
                    res.failed[n + 1] = r.error or "errore"

    def block(first: int, last: int) -> None:
        if out_of_quota.is_set():
            return text_block(first, last)
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            try:
                err = call(first, last, tmp)
            except NeedsHuman:                     # quota esaurita su tutte le chiavi (riserva compresa)
                if text_role is None:
                    raise
                out_of_quota.set()
                log(f"{pdf.name}: quota del modello a visione esaurita, passo alla modalità solo testo ({text_role.label})")
                return text_block(first, last)
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
        except NeedsHuman:              # quota esaurita ovunque e nessuna modalità di riserva: i checkpoint restano
            for f in futures:
                f.cancel()
            raise
    res.converted = len(todo) - len(res.failed)

    visual, text = _ckpt_map(ckpt, text=False), _ckpt_map(ckpt, text=True)
    if set(range(res.pages)) - set(visual) - set(text):     # incompleto: niente output, checkpoint intatti
        return res
    res.degraded = sorted(n + 1 for n in range(res.pages) if n not in visual)   # pagine solo-testo (1-based)
    files: list[Path] = []
    for n in range(res.pages):
        part = visual.get(n) or text[n]
        if not files or files[-1] != part:
            files.append(part)
    body = "\n\n".join(f.read_text(encoding="utf-8").strip() for f in files) + "\n"
    conversione = "visione" if not res.degraded else ("testo" if len(res.degraded) == res.pages else "misto")
    atomic_write_text(out, frontmatter.join({**(meta or {}), "conversione": conversione}, body))
    if not res.degraded:
        shutil.rmtree(ckpt, ignore_errors=True)           # con pagine solo-testo i checkpoint servono per l'upgrade
    res.output = out
    return res
