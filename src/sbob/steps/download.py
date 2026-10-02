"""Passo download: registrazioni Webex del Poli → video/<stem>.mp4.

Fonti (`sorgenti` del corso, anche più di una: si uniscono e una che non funziona non blocca le altre):
  archivio      archivio registrazioni del Poli (recman), letto col browser di `sbob login` → link_archivio.txt
  txt           file di link (link.txt), anche arricchito con data, forma didattica e argomento
  webeep        link Webex nei contenuti del corso WeBeep (API col token di `sbob login`)
  webpage-url   pagina web (es. sito del docente) · webpage-html: la stessa salvata in un file
Poi, per gli id nuovi: API Webex (cookie `ticket`) → link mp4 → aria2c (webex.py).

Identità di una registrazione = id del video Webex (manifest.videos: id → stem). I manifest creati col vecchio
downloader usano la chiave "YYYY-MM-DD HH-MM": si riconoscono e si ricollegano all'id al primo giro.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sbob import webex
from sbob.config import Course, Settings
from sbob.core import naming
from sbob.core.batch import list_inputs
from sbob.core.report import NeedsHuman, StepReport
from sbob.core.status import VIDEO_EXT
from sbob.steps.base import StepContext

_ID_RE = re.compile(r"[0-9a-f]{32}", re.I)
ALLOWED_COOKIES = ("ticket", "MoodleSession")
# "Forma didattica" dell'archivio recman → tipo di lezione
FORMA_TO_TIPO = {"lezione": "lez", "esercitazione": "ese", "laboratorio": "lab", "altro": "sem"}
_TITLE_TIPO = [(re.compile(r"\blab", re.I), "lab"), (re.compile(r"\b(?:esercitazion\w*|ese|es)\b", re.I), "ese"),
               (re.compile(r"\blez", re.I), "lez")]
LOGIN_ACTION = "sbob login"


def set_cookie(settings: Settings, nome: str, valore: str) -> None:
    """Salva un cookie nel file di sbob (permessi 600): il valore non passa mai su una riga di comando."""
    from sbob.core.secrets import save_cookie

    if nome not in ALLOWED_COOKIES:
        raise ValueError(f"cookie non gestito: {nome} (validi: {', '.join(ALLOWED_COOKIES)})")
    save_cookie(nome, valore.strip())


def tipo_from_title(title: str | None) -> str | None:
    """'2025-09-19 Lez 01 - introduzione' → lez; 'Esercitazione 3' → ese; 'Lab 2' → lab."""
    for pat, tipo in _TITLE_TIPO:
        if title and pat.search(title):
            return tipo
    return None


def read_links_file(path: Path) -> tuple[list[str], dict[str, dict]]:
    """File di link, anche arricchito: `link<TAB>dd/mm/yyyy HH:MM<TAB>forma didattica<TAB>argomento`.
    Righe vuote e `#` sono ignorate. Restituisce (link puliti per prd, {video_id: {data, tipo, argomento}})."""
    links, meta = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = [c.strip() for c in line.split("\t")]
        links.append(cols[0])
        if len(cols) > 1 and (m := _ID_RE.search(cols[0])):
            info: dict = {}
            try:
                info["data"] = datetime.strptime(cols[1], "%d/%m/%Y %H:%M")
            except ValueError:
                pass
            if len(cols) > 2 and cols[2]:
                info["tipo"] = FORMA_TO_TIPO.get(cols[2].lower())
            if len(cols) > 3 and cols[3]:
                info["argomento"] = cols[3]
            meta[m.group(0).lower()] = info
    return links, meta


def _links_file(course: Course, src: dict, options: dict) -> Path:
    file = Path(options.get("links") or src.get("file", "link.txt")).expanduser()
    file = file if file.is_absolute() else course.cartella / file
    if not file.is_file():
        raise NeedsHuman(f"File link non trovato: {file}", action=f"crea {file} con un link Webex per riga")
    return file


def webeep_links(course: Course, src: dict) -> list[tuple[str, dict]]:
    """Link Webex nei contenuti del corso WeBeep (moduli URL, descrizioni, riassunti delle sezioni).
    `url` della fonte può restringere: course/view.php?id=C[&section=S] oppure mod/<tipo>/view.php?id=<modulo>."""
    from sbob.auth.browser import load_token
    from sbob.webeep.client import WebeepClient

    q = parse_qs(urlparse(src.get("url") or "").query)
    path = urlparse(src.get("url") or "").path
    only_module = int(q["id"][0]) if "/mod/" in path and q.get("id") else None
    course_id = int(q["id"][0]) if "/course/view.php" in path and q.get("id") else course.webeep_id
    section = int(q["section"][0]) if q.get("section") else None
    if not course_id and not only_module:
        raise NeedsHuman(f"Per la fonte 'webeep' serve il corso WeBeep di {course.slug}",
                         action=f"sbob webeep collega {course.slug} <id>")
    client = WebeepClient(load_token() or "")
    if only_module and not course_id:
        course_id = client.call("core_course_get_course_module", cmid=only_module)["cm"]["course"]
    out: list[tuple[str, dict]] = []
    for sec in client.call("core_course_get_contents", courseid=course_id):
        if section is not None and sec.get("section") != section:
            continue
        if only_module is None:
            out += [(u, {}) for u in webex.links_in_html(sec.get("summary") or "")]
        for m in sec.get("modules", []):
            if only_module is not None and m.get("id") != only_module:
                continue
            title = (m.get("name") or "").strip() or None
            meta = {k: v for k, v in (("argomento", title), ("tipo", tipo_from_title(title))) if v}
            for c in m.get("contents") or []:
                url = c.get("fileurl") or ""
                if c.get("type") == "url" and webex.is_recording_link(url):
                    out.append((url, meta))
                elif m.get("modname") == "page" and c.get("filename") == "index.html":
                    out += [(u, {}) for u in webex.links_in_html(client.text(url))]
            out += [(u, meta) for u in webex.links_in_html(m.get("description") or "")]
    return out


def source_links(ctx: StepContext, src: dict, idx: int) -> list[tuple[str, dict]]:
    """[(link, metadati)] di UNA fonte. I metadati (data, tipo, argomento) vengono dalla fonte, se li ha."""
    course, tipo = ctx.course, src.get("tipo")
    if tipo == "archives":                           # vecchio tipo di prd per l'archivio: ora è `archivio`
        tipo, src = "archivio", {**src, "tipo": "archivio"}
    if tipo == "archivio":                           # → file di link arricchito, poi come una fonte txt
        tipo, src = "txt", {"tipo": "txt", "file": str(archive_links(ctx, src, idx))}
    ctx.log(f"download: leggo la fonte {idx + 1} ({tipo})…")
    if tipo == "txt":
        links, meta = read_links_file(_links_file(course, src, ctx.options))
        return [(u, meta.get(m.group(0).lower(), {}) if (m := _ID_RE.search(u)) else {}) for u in links]
    if tipo == "webpage-html":
        return [(u, {}) for u in webex.links_in_html(_links_file(course, src, ctx.options).read_text(errors="replace"))]
    if tipo == "webpage-url":
        import requests

        try:
            r = requests.get(src["url"], timeout=webex.TIMEOUT, headers={"User-Agent": "sbob"})
        except requests.RequestException as e:
            raise RuntimeError(f"pagina non raggiungibile: {type(e).__name__}") from None
        if r.status_code != 200:
            raise RuntimeError(f"pagina non raggiungibile: HTTP {r.status_code}")
        return [(u, {}) for u in webex.links_in_html(r.text)]
    if tipo == "webeep":
        return webeep_links(course, src)
    raise NeedsHuman(f"Fonte '{tipo}' sconosciuta per {course.slug}", action=f"correggi le sorgenti in [corsi.{course.slug}]")


def _ticket_session(ctx: StepContext, renew: bool = False):
    from sbob.core.secrets import load_cookie

    ticket = load_cookie("ticket")
    if (renew or not ticket) and try_renew_login(ctx):
        ticket = load_cookie("ticket")
    if not ticket:
        raise NeedsHuman("Manca l'accesso a Webex (cookie ticket)", action=LOGIN_ACTION)
    return webex.session(ticket)


def gather(ctx: StepContext, rep: StepReport) -> tuple[dict[str, dict], list[str], Exception | None]:
    """Tutte le fonti → {id video: metadati}, senza doppioni (vince la prima fonte che lo nomina)."""
    sources = ctx.course.sorgenti
    if not sources:
        raise NeedsHuman(f"Il corso '{ctx.course.slug}' non ha una fonte di registrazioni",
                         action=f"aggiungi `sorgenti = [...]` in [corsi.{ctx.course.slug}]")
    http = webex.session(None)                       # risolvere ldr.php non richiede il ticket
    found: dict[str, dict] = {}
    problems: list[str] = []
    first_error: Exception | None = None
    for idx, src in enumerate(sources):
        label = f"fonte {idx + 1} ({src.get('tipo')})"
        try:
            links = source_links(ctx, src, idx)
        except (NeedsHuman, RuntimeError) as e:
            problems.append(f"{label}: {e}")
            first_error = first_error or e
            continue
        bad = 0
        for url, meta in links:
            try:
                vid = webex.video_id(url, http)
            except RuntimeError:
                bad += 1
                continue
            if vid and vid not in found:
                found[vid] = dict(meta)
        if bad:
            rep.warnings.append(f"{label}: {bad} link non risolti (scaduti o rimossi?)")
    return found, problems, first_error


def try_renew_login(ctx: StepContext) -> bool:
    """Rinnovo silenzioso di ticket e cookie tramite il profilo di `sbob login`. False se non è possibile."""
    try:
        from sbob.auth.browser import PROFILE_DIR, login
    except ImportError:
        return False
    if not PROFILE_DIR.exists():
        return False
    ctx.log("download: cookie scaduti, provo a rinnovarli con `sbob login --rinnova`…")
    try:
        got = login(ctx.settings, headless=True, log=ctx.log)
    except Exception:  # noqa: BLE001 — sessione di Ateneo scaduta, Playwright assente, rete…
        return False
    return bool(got.get("ticket"))


def archive_links(ctx: StepContext, src: dict, idx: int = 0) -> Path:
    """Fonte `archivio`: raccoglie i link dall'archivio recman (browser di `sbob login`) e li scrive in
    <corso>/link_archivio.txt, nel formato di link.txt. Il file resta: si può controllare, correggere o riusare a mano."""
    from sbob.auth.recman import archive_entries, collect, write_links

    course = ctx.course
    entries = [src["url"]] if src.get("url") else []
    if not entries:
        if not course.webeep_id:
            raise NeedsHuman(f"Per la fonte 'archivio' serve il corso WeBeep di {course.slug}",
                             action=f"sbob webeep collega {course.slug} <id>  (oppure url = <link dell'archivio> nella fonte)")
        from sbob.auth.browser import load_token
        from sbob.webeep.client import WebeepClient
        entries = archive_entries(WebeepClient(load_token() or ""), course.webeep_id)
        if not entries:
            raise RuntimeError("il corso WeBeep non ha un modulo 'Archivio registrazioni'")
    rows: list[dict] = []
    for e in entries:
        rows += collect(e, headless=True, log=ctx.log)
    dst = course.cartella / ("link_archivio.txt" if idx == 0 else f"link_archivio_{idx + 1}.txt")
    write_links(list({r["webex"]: r for r in rows}.values()), dst, "archivio recman")
    return dst


def assign_names(course: Course, existing_stems: list[str], new: dict[str, str],
                 dates: dict[str, datetime] | None = None) -> dict[str, str]:
    """{key prd: tipo} → {key prd: stem canonico}, numerando per tipo dopo l'ultima lezione esistente di quel tipo.
    `dates` sovrascrive la data dedotta dal nome prd (che viene da Webex e a volte è sbagliata)."""
    parsed = [n for s in existing_stems if (n := naming.parse(s))]
    out: dict[str, str] = {}
    for tipo in sorted(set(new.values())):
        keys = [k for k, t in new.items() if t == tipo]
        dated = sorted(((dates or {}).get(k) or datetime.min, k) for k in keys)
        names = naming.next_numbers(parsed, [d.date() for d, _ in dated], course.slug, tipo)
        out.update({k: n.stem for (_, k), n in zip(dated, names)})
    return out


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("download")
    lay, course = ctx.layout, ctx.course
    tipo = ctx.options.get("tipo")          # None = dalla fonte (forma didattica, titolo) se c'è, altrimenti "lez"
    if tipo is not None and tipo not in naming.TIPI:
        rep.error = f"tipo '{tipo}' non valido ({', '.join(naming.TIPI)})"
        return rep
    manifest = ctx.manifest()

    # 1. quali registrazioni ci sono (tutte le fonti) e quali sono nuove
    found, problems, first_error = gather(ctx, rep)
    if problems and not found and first_error:
        raise first_error                                # nessuna fonte ha funzionato: errore vero
    rep.warnings += [f"{p} (le altre fonti sono state lette)" for p in problems]
    if not found:
        rep.notes.append("Nessuna registrazione trovata nelle fonti.")
        return rep
    videos = manifest.videos
    unknown = [v for v in found if ctx.force or v not in videos]
    rep.skipped = sorted(videos[v] for v in found if v in videos and not ctx.force)

    # 2. informazioni Webex (data, link) solo per gli id non ancora noti
    infos: dict[str, webex.Recording | Exception] = {}
    if unknown:
        http = _ticket_session(ctx)
        try:
            infos = webex.recordings(unknown, http)
        except webex.TicketError:                    # scaduto: rinnovo silenzioso e un secondo tentativo
            try:
                infos = webex.recordings(unknown, _ticket_session(ctx, renew=True))
            except webex.TicketError:
                raise NeedsHuman("Accesso a Webex scaduto (cookie ticket)", action=LOGIN_ACTION) from None
    recs: dict[str, webex.Recording] = {}
    relinked = False
    for vid, info in infos.items():
        if isinstance(info, Exception):
            rep.fail(vid[:8], str(info))
            continue
        if not ctx.force and info.legacy_key in videos:       # già scaricata col vecchio downloader
            videos[vid] = videos[info.legacy_key]
            rep.skipped.append(videos[vid])
            relinked = True
            continue
        recs[vid] = info
    if relinked and not ctx.dry_run:
        manifest.save()

    existing = [p.stem for p in list_inputs(lay.video, VIDEO_EXT)]
    names = assign_names(course, existing,
                         {v: tipo or found[v].get("tipo") or "lez" for v in recs},
                         {v: found[v].get("data") or r.created for v, r in recs.items()})
    if ctx.only:
        names = {v: s for v, s in names.items() if s in ctx.only}
    rep.skipped = sorted(set(rep.skipped))
    if ctx.dry_run or not names:
        rep.done = sorted(names.values())
        return rep

    # 3. scarico dei soli nuovi (staging/dl resta tra un giro e l'altro: aria2c riprende i file a metà)
    dl_dir = lay.staging / "dl"
    ctx.log(f"download: scarico {len(names)} registrazioni…")
    got = webex.download([(recs[v], s) for v, s in names.items()], dl_dir, log=ctx.log)
    lay.ensure("video")
    for vid, stem in names.items():
        src = got.get(vid)
        if not src:
            rep.fail(stem, "non scaricata (rilancia per riprendere)")
            continue
        dst = lay.video / f"{stem}.mp4"
        shutil.move(str(src), dst)
        videos[vid] = stem
        if found[vid].get("argomento"):
            manifest.meta(stem)["argomento"] = found[vid]["argomento"]
        manifest.save()
        rep.done.append(stem)
        rep.outputs.append(str(dst))
    return rep
