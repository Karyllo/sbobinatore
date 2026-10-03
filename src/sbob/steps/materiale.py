"""Passo materiale: WeBeep → <corso>/materiale/ (sync incrementale) → <corso>/materiale_md/ (Markdown per l'agente).

1. Sync (solo corsi con `webeep_id`): scarica i file nuovi o modificati; i file spariti da WeBeep NON si cancellano.
1b. Siti personali dei docenti (`materiale_siti = ["https://..."]`): file linkati sulla pagina, solo dallo stesso sito.
2. Conversione di tutto `materiale/` (anche file aggiunti a mano), solo di ciò che è nuovo o cambiato (sha256):
   - .pdf                    → modello a visione (ruolo `pdf`, riserva automatica); se la quota finisce, modalità
                               "solo testo" (ruolo `pdf_testo`) con avviso, e al run dopo si rifanno con la visione
   - .pptx/.docx/.xlsx…      → via LibreOffice in PDF (così si vedono le figure) oppure markitdown (solo testo)
   - testo e codice          → copia in Markdown (gratis)
   - altro (video, zip…)     → solo elencato
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from sbob.config import install_command
from sbob.core import frontmatter
from sbob.core.batch import atomic_write_text, list_tree
from sbob.core.report import NeedsHuman, StepReport
from sbob.llm.cost import CostTracker
from sbob.llm.registry import Registry, parse_model_override
from sbob.steps.base import StepContext
from sbob.tools.pdf2md import DEFAULT_PAGES_PER_BLOCK, convert_pdf, text_fallback_role
from sbob.webeep.client import MissingOnServer, safe_path

PDF_EXT = {".pdf"}
OFFICE_EXT = {".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".odp", ".odt"}
TEXT_EXT = {".md", ".txt", ".tex", ".csv", ".json", ".py", ".m", ".c", ".cpp", ".h", ".java", ".js", ".ipynb", ".r",
            ".sh", ".sql", ".yml", ".yaml", ".toml"}
_LANG = {".py": "python", ".m": "matlab", ".c": "c", ".cpp": "cpp", ".h": "c", ".java": "java", ".js": "javascript",
         ".r": "r", ".sh": "bash", ".sql": "sql", ".json": "json", ".csv": "csv", ".tex": "latex", ".yml": "yaml",
         ".yaml": "yaml", ".toml": "toml"}
# L'inizio della parola conta (niente lettera prima: "elaborato" non è "lab"); la fine no (laboratori, Lab03, esame_2022)
_TIPO_RULES = (("tde", re.compile(r"(?<![a-z])(tde|temi?[\s_]*d.?esame|esam[ei]|appell[oi]|compit[oi]|prova[\s_]*scritta)", re.I)),
               ("laboratorio", re.compile(r"(?<![a-z])lab", re.I)),
               ("esercitazione", re.compile(r"(?<![a-z])(esercit|esercizi|soluzion|problemi)", re.I)))


def infer_tipo(relpath: str) -> str:
    """tde | laboratorio | esercitazione | slide, dal percorso (sezione + nome del file)."""
    for tipo, rx in _TIPO_RULES:
        if rx.search(relpath):
            return tipo
    return "slide"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _text_to_md(path: Path) -> str:
    ext = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".md", ".txt"):
        return raw
    if ext == ".ipynb":
        try:
            cells = json.loads(raw).get("cells", [])
        except json.JSONDecodeError:
            return f"```\n{raw}\n```"
        out = []
        for c in cells:
            src = "".join(c.get("source", []))
            out.append(src if c.get("cell_type") == "markdown" else f"```python\n{src}\n```")
        return "\n\n".join(out)
    fence = "`" * (max((len(m) for m in re.findall(r"`+", raw)), default=2) + 1)     # fence più lungo di quelli interni
    return f"{fence}{_LANG.get(ext, '')}\n{raw}\n{fence}"


def _office_to_pdf(src: Path, tmp: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    res = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(src)],
                         capture_output=True, text=True, timeout=300)
    pdf = tmp / f"{src.stem}.pdf"
    return pdf if res.returncode == 0 and pdf.exists() else None


_RESULTS = re.compile(r"\b(esit[io]|graduatoria|verbalizzazion\w*)\b", re.I)          # sempre elenchi di studenti
_RESULTS_WORDS = re.compile(r"\b(risultati|voti)\b", re.I)                             # ambigue: "Risultati notevoli…" è materiale vero
_RESULTS_CONTEXT = re.compile(r"\b(esam\w*|appell\w*|prov[ae]|compitin\w*|itinere)\b", re.I)


def is_results_list(name: str) -> bool:
    """'Esiti AM1 - 03-07-26.pdf', 'Risultati prova', 'Graduatoria': liste con i dati (matricole, voti) di altri studenti.
    'Risultati' e 'voti' da soli non bastano ("Risultati notevoli di analisi" è materiale): servono anche esame, appello,
    prova, compitino o in itinere nel nome."""
    stem = Path(name).stem
    return bool(_RESULTS.search(stem) or (_RESULTS_WORDS.search(stem) and _RESULTS_CONTEXT.search(stem)))


def _office_to_text(src: Path) -> str | None:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    return MarkItDown().convert(str(src)).text_content


# ------------------------------------------------------------------ sync

def sync(ctx: StepContext, rep: StepReport) -> None:
    from sbob.auth.browser import load_token
    from sbob.webeep.client import WebeepClient

    course, lay, manifest = ctx.course, ctx.layout, ctx.manifest()
    client = WebeepClient(load_token() or "")
    files = client.files(course.webeep_id or 0)
    todo = []
    for f in files:
        dst = lay.materiale / f.relpath
        known = manifest.materiale.get(f.key, {})
        if not dst.exists() or known.get("modified") != f.modified or known.get("size") != f.size:
            todo.append((f, dst))
        else:
            rep.skipped.append(f.relpath)
    gone = sorted(set(manifest.materiale) - {f.key for f in files})
    if gone:
        rep.notes.append(f"{len(gone)} file non più su WeBeep (tenuti in locale): {', '.join(gone[:3])}"
                         + ("…" if len(gone) > 3 else ""))
    if ctx.dry_run:
        rep.done += [f.relpath for f, _ in todo]
        return
    ctx.log(f"materiale: {len(todo)} file da scaricare ({len(files)} su WeBeep)")
    missing: list[tuple] = []
    for f, dst in todo:
        try:
            client.download(f, dst)
        except NeedsHuman:
            raise
        except MissingOnServer:              # 404 dal percorso normale: si riprova dopo con la cartella zip
            missing.append((f, dst))
            continue
        except Exception as e:  # noqa: BLE001
            rep.fail(f.relpath, e)
            continue
        manifest.materiale[f.key] = {"modified": f.modified, "size": f.size}
        manifest.save()
        rep.done.append(f.relpath)
        rep.outputs.append(str(dst))
    if missing:
        _from_folder_zip(ctx, client, missing, manifest, rep)


def _from_folder_zip(ctx: StepContext, client, missing: list[tuple], manifest, rep: StepReport) -> None:
    """File che il percorso normale non raggiunge (404): si prendono dallo zip della cartella del modulo.
    Quelli che non ci sono nemmeno lì restano una nota (file rotto su WeBeep)."""
    from sbob.core.secrets import load_cookie

    by_module: dict[int, list[tuple]] = {}
    for f, dst in missing:
        by_module.setdefault(f.module, []).append((f, dst))
    session = load_cookie("MoodleSession")
    for module, items in by_module.items():
        if items[0][0].modname and items[0][0].modname != "folder":      # risorsa singola: non esiste uno zip da cui riprenderla
            for f, _ in items:
                rep.notes.append(f"{f.relpath}: non scaricabile da WeBeep (è una risorsa singola, non una cartella: "
                                 "il file è rotto sul sito, non c'è una riserva)")
            continue
        zf = client.folder_zip(module, session) if (session and module) else None
        try:
            _take_from_zip(ctx, zf, items, manifest, rep)
        finally:
            if zf is not None:
                zf.close()                                                  # cancella lo zip temporaneo


def _take_from_zip(ctx: StepContext, zf, items: list[tuple], manifest, rep: StepReport) -> None:
    names = set(zf.namelist()) if zf else set()
    for f, dst in items:
        if f.inner not in names:
            why = "login scaduto? prova `sbob login`" if zf is None else "non c'è nemmeno nello zip della cartella"
            rep.notes.append(f"{f.relpath}: non scaricabile da WeBeep ({why})")
            continue
        tmp = dst.with_name(f".dl-{dst.name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(f.inner) as src_fh, open(tmp, "wb") as out_fh:       # a pezzi, non tutto il file in memoria
            shutil.copyfileobj(src_fh, out_fh)
        if f.modified:
            os.utime(tmp, (f.modified, f.modified))
        tmp.replace(dst)
        manifest.materiale[f.key] = {"modified": f.modified, "size": f.size}
        manifest.save()
        rep.done.append(f.relpath)
        rep.outputs.append(str(dst))
        ctx.log(f"materiale: {f.relpath} preso dallo zip della cartella")


# ------------------------------------------------------------------ siti personali dei docenti

SITE_EXT = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".odp", ".odt", ".tex", ".txt", ".md", ".py",
            ".m", ".ipynb"}
SITE_MAX_BYTES = 200 * 1024 * 1024


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" and (href := dict(attrs).get("href")):
            self.hrefs.append(href)


def _is_public_host(host: str) -> bool:
    """Rifiuta host che puntano alla rete locale: i link di una pagina web sono input non fidato."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return all(not (ipaddress.ip_address(i[4][0]).is_private or ipaddress.ip_address(i[4][0]).is_loopback
                    or ipaddress.ip_address(i[4][0]).is_link_local) for i in infos)


def site_links(html: str, page_url: str) -> tuple[list[str], int]:
    """(link a file scaricabili sullo STESSO sito della pagina, numero di link ad altri siti ignorati)."""
    parser = _Links()
    parser.feed(html)
    base = urlparse(page_url)
    same, other = [], 0
    for href in dict.fromkeys(parser.hrefs):
        u = urlparse(urljoin(page_url, href.split("#")[0]))
        if u.scheme not in ("http", "https") or Path(unquote(u.path)).suffix.lower() not in SITE_EXT:
            continue
        if u.netloc == base.netloc:
            same.append(u.geturl())
        else:
            other += 1
    return same, other


def sync_siti(ctx: StepContext, rep: StepReport, session=None) -> None:
    import requests

    course, lay, manifest = ctx.course, ctx.layout, ctx.manifest()
    http = session or requests.Session()
    for page_url in course.extra.get("materiale_siti", []):
        host = urlparse(page_url).netloc
        section = safe_name_site(host)
        try:
            if urlparse(page_url).scheme not in ("http", "https") or not _is_public_host(urlparse(page_url).hostname or ""):
                raise RuntimeError("indirizzo non valido o non pubblico")
            r = http.get(page_url, timeout=30)
            r.raise_for_status()
            links, other = site_links(r.text, r.url)
        except Exception as e:  # noqa: BLE001
            rep.fail(page_url, f"pagina non accessibile ({type(e).__name__}: {str(e)[:80]})")
            continue
        if other:
            rep.notes.append(f"{host}: {other} link ad altri siti non scaricati")
        for url in links:
            rel = f"{section}/{safe_path(unquote(urlparse(url).path.lstrip('/')))}"
            dst = lay.materiale / rel
            known = manifest.materiale.get(rel, {})
            headers = {}
            if dst.exists() and known.get("etag"):
                headers["If-None-Match"] = known["etag"]
            elif dst.exists() and known.get("last_modified"):
                headers["If-Modified-Since"] = known["last_modified"]
            if ctx.dry_run:
                (rep.skipped if dst.exists() and known else rep.done).append(rel)
                continue
            try:
                with http.get(url, headers=headers, stream=True, timeout=60) as resp:
                    if resp.status_code == 304:
                        rep.skipped.append(rel)
                        continue
                    resp.raise_for_status()
                    if int(resp.headers.get("Content-Length") or 0) > SITE_MAX_BYTES:
                        rep.fail(rel, "file troppo grande (>200 MB), saltato")
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".dl-")
                    size = 0
                    try:
                        with os.fdopen(fd, "wb") as out:
                            for chunk in resp.iter_content(1 << 16):
                                size += len(chunk)
                                if size > SITE_MAX_BYTES:
                                    raise RuntimeError("file troppo grande (>200 MB)")
                                out.write(chunk)
                        os.replace(tmp, dst)
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                    new_hash_changed = known.get("size") != size
                    manifest.materiale[rel] = {"etag": resp.headers.get("ETag"),
                                               "last_modified": resp.headers.get("Last-Modified"), "size": size}
                    manifest.save()
                    if headers or new_hash_changed or not known:
                        rep.done.append(rel)
                        rep.outputs.append(str(dst))
            except Exception as e:  # noqa: BLE001
                rep.fail(rel, f"{type(e).__name__}: {str(e)[:80]}")


def safe_name_site(host: str) -> str:
    from sbob.webeep.client import safe_name
    return f"Sito {safe_name(host)}"


# ------------------------------------------------------------------ conversione

def convert_all(ctx: StepContext, rep: StepReport) -> None:
    course, lay, manifest = ctx.course, ctx.layout, ctx.manifest()
    sources = list_tree(lay.materiale, PDF_EXT | OFFICE_EXT | TEXT_EXT)
    others = [p for p in list_tree(lay.materiale, {p.suffix for p in lay.materiale.rglob("*") if p.is_file()})
              if p.suffix.lower() not in PDF_EXT | OFFICE_EXT | TEXT_EXT] if lay.materiale.is_dir() else []
    if others:
        rep.notes.append(f"{len(others)} file non convertibili (video, archivi, immagini…), lasciati come sono")

    todo, degraded_pending = [], []
    privacy_skipped: list[str] = []
    for src in sources:
        rel = src.relative_to(lay.materiale).as_posix()
        if is_results_list(src.name):             # elenchi di esiti: dati di altri studenti, mai a un modello né al taccuino
            privacy_skipped.append(rel)
            continue
        if ctx.only and Path(rel).stem not in ctx.only:
            continue
        if (folder := ctx.options.get("cartella")) and folder.lower() not in rel.lower():
            continue                         # --cartella: solo i file il cui percorso contiene questo testo
        h = sha256(src)
        prev = manifest.materiale_conv.get(rel, {})
        out = lay.materiale_md / f"{rel}.md"
        if prev.get("sha") == h and out.exists() and not ctx.force:
            if prev.get("conv") in ("testo", "misto"):
                degraded_pending.append((src, rel, h, out))      # riprovo con la visione: magari c'è di nuovo quota
            else:
                rep.skipped.append(f"{rel}.md")
            continue
        todo.append((src, rel, h, out))
    todo += degraded_pending
    if privacy_skipped:
        first = ", ".join(Path(r).name for r in privacy_skipped[:3])
        more = f" e altri {len(privacy_skipped) - 3}" if len(privacy_skipped) > 3 else ""
        rep.notes.append(f"{len(privacy_skipped)} elenchi di esiti non convertiti (contengono dati di altri studenti): "
                         f"{first}{more}. Se uno è materiale vero, rinominalo")
    if ctx.dry_run:
        rep.done += [f"{rel}.md" for _, rel, _, _ in todo]
        return
    if not todo:
        return

    tracker = CostTracker(log_path=lay.costs)
    registry = Registry(ctx.settings, tracker)
    pdf_role = text_role = None
    n_pages = int(course.extra.get("pagine_per_blocco", DEFAULT_PAGES_PER_BLOCK))
    degraded: list[str] = []
    try:
        for src, rel, h, out in todo:
            ext = src.suffix.lower()
            meta = {"corso": course.nome, "slug": course.slug, "anno": course.anno_accademico,
                    "edizione": course.anno_accademico, "fonte": rel,
                    "sezione": rel.split("/")[0] if "/" in rel else None, "tipo": infer_tipo(rel)}
            try:
                if ext in TEXT_EXT:
                    atomic_write_text(out, frontmatter.join({**meta, "conversione": "copia"}, _text_to_md(src)))
                    conv = "copia"
                else:
                    with tempfile.TemporaryDirectory() as t:
                        pdf = src
                        if ext in OFFICE_EXT:
                            converted = _office_to_pdf(src, Path(t))
                            pdf = converted if converted is not None else src
                            if converted is None:                                  # niente LibreOffice: solo testo
                                text = _office_to_text(src)
                                if text is None:
                                    rep.fail(rel, "serve LibreOffice (`brew install --cask libreoffice`) o "
                                                  f"l'extra office (`{install_command('pdf', 'office')}`)")
                                    continue
                                atomic_write_text(out, frontmatter.join({**meta, "conversione": "testo-office"}, text))
                                manifest.materiale_conv[rel] = {"sha": h, "conv": "testo-office"}
                                manifest.save()
                                rep.done.append(f"{rel}.md")
                                continue
                        if pdf_role is None:
                            pdf_role = registry.role("pdf", parse_model_override(ctx.options.get("modello")))
                            text_role = text_fallback_role(ctx.settings, registry)
                        key = hashlib.sha256(rel.encode()).hexdigest()[:12]
                        r = convert_pdf(pdf, out, pdf_role, course.lingua, meta=meta, text_role=text_role, upgrade=True,
                                        force=ctx.force, pages_per_block=n_pages,
                                        checkpoint_dir=lay.state / "pdf_checkpoints" / key, log=ctx.log)
                        if not r.complete and not r.failed:
                            # nessuna pagina fallita: sono scansioni che aspettano un modello a visione (quota finita)
                            rep.warnings.append(f"{rel}: scansione in attesa di un modello a visione (quota esaurita); "
                                                "si completa al prossimo giro")
                            continue
                        if not r.complete:
                            pages = ", ".join(map(str, sorted(r.failed)))
                            rep.fail(rel, f"{len(r.failed)}/{r.pages} pagine fallite ({pages}); rilancia per riprovare")
                            continue
                        conv = "visione" if not r.degraded else ("testo" if len(r.degraded) == r.pages else "misto")
                        if r.degraded:
                            degraded.append(f"{rel} ({len(r.degraded)}/{r.pages} pagine solo testo)")
            except NeedsHuman:
                raise
            except Exception as e:  # noqa: BLE001
                rep.fail(rel, e)
                continue
            manifest.materiale_conv[rel] = {"sha": h, "conv": conv}
            manifest.save()
            rep.done.append(f"{rel}.md")
            rep.outputs.append(str(out))
    finally:
        rep.cost = tracker.summary()
    if degraded:
        rep.warnings.append("quota esaurita: " + "; ".join(degraded) + ". Le pagine senza figure trascritte "
                            "verranno rifatte con la visione al prossimo `sbob materiale`.")


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("materiale")
    lay = ctx.layout
    if ctx.course.webeep_id:
        sync(ctx, rep)
    if ctx.course.extra.get("materiale_siti"):
        sync_siti(ctx, rep)
    if not ctx.course.webeep_id and not ctx.course.extra.get("materiale_siti") and not lay.materiale.is_dir():
        rep.notes.append("Nessun materiale: collega il corso a WeBeep (`sbob webeep collega`) o metti i file in materiale/.")
        return rep
    converti = ctx.options.get("converti")
    if converti is None:                    # opzione > `converti` del corso > [materiale] converti (false = solo scarico)
        converti = ctx.course.extra.get("converti", (ctx.settings.raw.get("materiale", {}) or {}).get("converti", True))
    if not converti:
        rep.notes.append("conversione disattivata: file scaricati ma non convertiti (sbob materiale <corso> --converti)")
        return rep
    if not ctx.dry_run:
        lay.ensure("materiale_md")
    convert_all(ctx, rep)
    return rep
