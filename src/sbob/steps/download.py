"""Passo download: registrazioni Polimi → video/<stem>.mp4.

Usa il downloader `prd` in un ambiente separato (vincola typer 0.6 e click < 8.2, incompatibili con sbob):
  - `downloader` = cartella con .venv  → si usa quel venv (sviluppo locale)
  - altrimenti (cartella o "git+https://...") → `uv run --no-project --with <spec>`: uv installa e mette in cache

Flusso in due fasi, così una ripetizione non riscarica nulla:
  1. piano   `prd <tipo> ... --no-aria2c` → xlsx con data e link (nessun download)
  2. scarico `prd txt ids.txt` con SOLO gli id non ancora nel manifest, in staging/dl

Identità di una registrazione = nome che prd le dà, "YYYY-MM-DD HH-MM" (manifest.videos: chiave → stem).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook  # type: ignore[import-untyped]

from sbob.config import Course, Settings
from sbob.core import naming
from sbob.core.batch import atomic_write_text, list_inputs
from sbob.core.report import NeedsHuman, StepReport
from sbob.core.status import VIDEO_EXT
from sbob.steps.base import StepContext

_ID_RE = re.compile(r"[0-9a-f]{32}", re.I)
_TICKET_MSGS = ("refresh",                    # prd: "Try refreshing the ticket"
                "downloadrecordinginfo")      # KeyError di prd quando Webex risponde senza dati (ticket non valido)


def prd_command(settings: Settings) -> tuple[list[str], dict[str, str]]:
    """(comando per lanciare `python -m prd`, variabili d'ambiente)."""
    spec = settings.downloader
    local = Path(spec).expanduser() if not spec.startswith(("git+", "http")) else None
    if local is not None and (local / ".venv" / "bin" / "python").exists():
        # clone con il suo venv: prd non è installato lì, si importa dalla cartella
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(local), os.environ.get("PYTHONPATH")]))}
        return [str(local / ".venv" / "bin" / "python"), "-m", "prd"], env
    if local is not None and not local.exists():
        raise NeedsHuman(f"Downloader non trovato in {local}",
                         action="correggi `downloader` in sbob.toml (cartella del clone o indirizzo git+https://...)")
    uv = shutil.which("uv")
    if not uv:
        raise NeedsHuman("Serve uv per installare il downloader",
                         action="curl -LsSf https://astral.sh/uv/install.sh | sh")
    return [uv, "run", "--no-project", "--quiet", "--with", str(local or spec), "python", "-m", "prd"], dict(os.environ)


def _run_prd(settings: Settings, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    cwd.mkdir(parents=True, exist_ok=True)
    cmd, env = prd_command(settings)
    return subprocess.run([*cmd, *args], cwd=cwd, text=True, capture_output=True, env=env)


ALLOWED_COOKIES = ("ticket", "MoodleSession", "JSESSIONID", "INGRESSCOOKIE", "SSL_JSESSIONID")


def set_cookie(settings: Settings, nome: str, valore: str) -> None:
    """Salva un cookie per il downloader scrivendo direttamente il suo file (permessi 600): il valore non passa
    sulla riga di comando di un sottoprocesso, dove sarebbe visibile ad altri processi."""
    from sbob.core.secrets import save_prd_cookie

    if nome not in ALLOWED_COOKIES:
        raise ValueError(f"cookie non gestito: {nome} (validi: {', '.join(ALLOWED_COOKIES)})")
    save_prd_cookie(nome, valore.strip())


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


def _source_args(course: Course, src: dict, options: dict, course_dir: Path, staging: Path | None = None,
                 idx: int = 0) -> tuple[list[str], dict]:
    tipo = src.get("tipo")
    if not tipo:
        raise NeedsHuman(f"Il corso '{course.slug}' non ha una sorgente", action=f"aggiungi sorgente in [corsi.{course.slug}]")
    common = ["--course", course.slug, "--academic-year", course.anno_accademico]
    if tipo in ("txt", "webpage-html"):
        file = Path(options.get("links") or src.get("file", "link.txt")).expanduser()
        file = file if file.is_absolute() else course_dir / file
        if not file.is_file():
            raise NeedsHuman(f"File link non trovato: {file}", action=f"crea {file} con un link Webex per riga")
        if tipo == "txt" and staging is not None:
            links, meta = read_links_file(file)      # a prd vanno solo i link; i metadati li usa sbob
            clean = staging / f"links{idx}.txt"
            atomic_write_text(clean, "\n".join(links) + "\n")
            return [tipo, str(clean), *common], meta
        return [tipo, str(file), *common], {}
    if tipo == "webpage-url":
        return [tipo, src["url"], *common], {}
    return [tipo, src["url"]], {}  # webeep, archives: corso e anno li ricava prd dalla pagina


# Quali cookie servono a prd per ogni sorgente: ticket (Webex) sempre, più quello del sito da cui si leggono i link
COOKIES_FOR_SOURCE = {
    "webeep": ("ticket", "MoodleSession"),
    "archives": ("ticket", "JSESSIONID", "INGRESSCOOKIE"),   # archivio su onlineservices.polimi.it
}
_COOKIE_WHERE = {"ticket": "politecnicomilano.webex.com", "MoodleSession": "webeep.polimi.it",
                 "JSESSIONID": "onlineservices.polimi.it (archivio registrazioni)",
                 "INGRESSCOOKIE": "onlineservices.polimi.it (archivio registrazioni)",
                 "SSL_JSESSIONID": "www11.ceda.polimi.it (vecchio archivio)"}
# "Forma didattica" dell'archivio recman (prd la mette nel subject come "[Forma] argomento") → tipo di lezione
FORMA_TO_TIPO = {"lezione": "lez", "esercitazione": "ese", "laboratorio": "lab", "altro": "sem"}
_FORMA = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.S)


def cookie_help(tipo: str | None) -> str:
    needed = COOKIES_FOR_SOURCE.get(tipo or "", ("ticket",))
    return "; ".join(f"copia il cookie '{c}' da {_COOKIE_WHERE[c]} e lancia: sbob cookie {c} <valore>" for c in needed)


def _check_output(res: subprocess.CompletedProcess, tipo: str | None = None) -> None:
    text = (res.stdout or "") + (res.stderr or "")
    lowered = text.lower()
    if res.returncode != 0 and ("moodlesession" in lowered or "jsessionid" in lowered or "cookie" in lowered
                                or "zero recordings" in lowered or any(m in lowered for m in _TICKET_MSGS)):
        needed = " e ".join(COOKIES_FOR_SOURCE.get(tipo or "", ("ticket",)))
        raise NeedsHuman(f"Cookie scaduti o mancanti (per questa sorgente servono: {needed})",
                         action="sbob login  (oppure, a mano: " + cookie_help(tipo) + ")")
    if res.returncode != 0:
        tail = [l for l in text.strip().splitlines() if l.strip()][-3:]
        raise RuntimeError("prd fallito: " + " | ".join(tail))


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


def plan_source(ctx: StepContext, src: dict, idx: int) -> dict[str, dict]:
    """Elenco delle registrazioni di UNA fonte (nessun download), con i metadati del file di link se presenti."""
    lay, course = ctx.layout, ctx.course
    if src.get("tipo") == "archivio":                # → file di link arricchito, poi come una fonte txt
        src = {"tipo": "txt", "file": str(archive_links(ctx, src, idx))}
    plan_dir = lay.staging / f"plan{idx}"
    shutil.rmtree(plan_dir, ignore_errors=True)
    args, link_meta = _source_args(course, src, ctx.options, course.cartella, lay.staging, idx)
    ctx.log(f"download: leggo la fonte {idx + 1} ({src.get('tipo')})…")
    plan_args = [*args, "--no-aria2c", "--create-xlsx", "--output", str(plan_dir)]
    try:
        _check_output(_run_prd(ctx.settings, plan_args, lay.state), src.get("tipo"))
    except NeedsHuman:
        # cookie scaduti: se c'è un profilo di `sbob login`, si rinnovano da soli e si riprova una volta
        if not try_renew_login(ctx):
            raise
        shutil.rmtree(plan_dir, ignore_errors=True)
        _check_output(_run_prd(ctx.settings, plan_args, lay.state), src.get("tipo"))
    plan = read_plan(plan_dir)
    for info in plan.values():                   # metadati del file di link (archivio): prevalgono
        extra = link_meta.get(str(info["id"]).lower(), {})
        info["tipo"] = extra.get("tipo") or info["tipo"]
        info["argomento"] = extra.get("argomento") or info["argomento"]
        info["data"] = extra.get("data")
    return plan


def read_plan(staging_plan: Path) -> dict[str, dict[str, str | None]]:
    """{ 'YYYY-MM-DD HH-MM': {"id", "tipo", "argomento"} } da tutti gli xlsx generati da prd.
    `tipo` è dedotto dalla forma didattica quando la sorgente la fornisce (archivio recman), altrimenti None."""
    found: dict[str, dict[str, str | None]] = {}
    for xlsx in sorted(staging_plan.rglob("*.xlsx")):
        ws = load_workbook(xlsx).active
        for row in ws.iter_rows(min_row=2):
            link = row[0].hyperlink.target if row[0].hyperlink else None
            when = row[2].value
            if not link or not when or not (m := _ID_RE.search(link)):
                continue
            dt = when if isinstance(when, datetime) else datetime.strptime(str(when), "%Y-%m-%d %H:%M")
            subject = str(row[3].value or "").strip() if len(row) > 3 else ""
            tipo, argomento = None, subject or None
            if fm := _FORMA.match(subject):
                tipo = FORMA_TO_TIPO.get(fm.group(1).strip().lower())
                argomento = fm.group(2).strip() or None
            found[dt.strftime("%Y-%m-%d %H-%M")] = {"id": m.group(0), "tipo": tipo, "argomento": argomento}
    return found


def assign_names(course: Course, existing_stems: list[str], new: dict[str, str],
                 dates: dict[str, datetime] | None = None) -> dict[str, str]:
    """{key prd: tipo} → {key prd: stem canonico}, numerando per tipo dopo l'ultima lezione esistente di quel tipo.
    `dates` sovrascrive la data dedotta dal nome prd (che viene da Webex e a volte è sbagliata)."""
    parsed = [n for s in existing_stems if (n := naming.parse(s))]
    out: dict[str, str] = {}
    for tipo in sorted(set(new.values())):
        keys = [k for k, t in new.items() if t == tipo]
        dated = sorted(((dates or {}).get(k) or naming.parse_prd(k) or datetime.min, k) for k in keys)
        names = naming.next_numbers(parsed, [d.date() for d, _ in dated], course.slug, tipo)
        out.update({k: n.stem for (_, k), n in zip(dated, names)})
    return out


def run(ctx: StepContext) -> StepReport:
    rep = ctx.report("download")
    lay, course = ctx.layout, ctx.course
    tipo = ctx.options.get("tipo")          # None = dalla forma didattica se c'è, altrimenti "lez"
    if tipo is not None and tipo not in naming.TIPI:
        rep.error = f"tipo '{tipo}' non valido ({', '.join(naming.TIPI)})"
        return rep
    manifest = ctx.manifest()

    # 1. piano: ogni fonte produce il suo elenco; si uniscono (stesso video in due posti = una sola registrazione)
    sources = course.sorgenti
    if not sources:
        raise NeedsHuman(f"Il corso '{course.slug}' non ha una fonte di registrazioni",
                         action=f"aggiungi `sorgente = {{...}}` (o `sorgenti = [...]`) in [corsi.{course.slug}]")
    plan: dict[str, dict] = {}
    seen_ids: set[str] = set()
    problems: list[str] = []
    first_error: Exception | None = None
    for idx, src in enumerate(sources):
        label = f"fonte {idx + 1} ({src.get('tipo')})"
        try:
            part = plan_source(ctx, src, idx)
        except NeedsHuman as e:
            problems.append(f"{label}: {e}")
            first_error = first_error or e
            continue
        except RuntimeError as e:
            problems.append(f"{label}: {e}")
            first_error = first_error or e
            continue
        for key, info in part.items():
            if key in plan or info["id"].lower() in seen_ids:
                continue                                 # già trovata in un'altra fonte
            plan[key] = info
            seen_ids.add(info["id"].lower())
    if problems and not plan and first_error:
        raise first_error                                # nessuna fonte ha funzionato: errore vero
    rep.warnings += [f"{p} (le altre fonti sono state lette)" for p in problems]
    if not plan:
        rep.notes.append("Nessuna registrazione trovata nelle fonti.")
        return rep

    known = set() if ctx.force else set(manifest.videos)
    new_keys = sorted(k for k in plan if k not in known)
    rep.skipped = sorted(manifest.videos[k] for k in plan if k in known)
    existing = [p.stem for p in list_inputs(lay.video, VIDEO_EXT)]
    names = assign_names(course, existing, {k: tipo or plan[k]["tipo"] or "lez" for k in new_keys},
                         {k: plan[k]["data"] for k in new_keys if plan[k].get("data")})
    if ctx.only:
        names = {k: s for k, s in names.items() if s in ctx.only}
    if ctx.dry_run or not names:
        rep.done = sorted(names.values())
        return rep

    # 2. scarico dei soli nuovi
    dl_dir = lay.staging / "dl"
    shutil.rmtree(dl_dir, ignore_errors=True)
    dl_dir.mkdir(parents=True)          # prd crea --output solo quando genera l'xlsx (qui disattivato)
    ids = lay.staging / "ids.txt"
    atomic_write_text(ids, "\n".join(plan[k]["id"] for k in names) + "\n")
    ctx.log(f"download: scarico {len(names)} registrazioni…")
    _check_output(_run_prd(ctx.settings, ["txt", str(ids), "--course", course.slug, "--academic-year",
                                          course.anno_accademico, "--no-create-xlsx", "--output", str(dl_dir)],
                           lay.state))
    if leftovers := list(dl_dir.rglob("*.aria2")):
        rep.error = f"download incompleto: {len(leftovers)} file .aria2 rimasti in {dl_dir}; rilancia per riprendere"
        return rep

    lay.ensure("video")
    got = {p.stem: p for p in dl_dir.rglob("*.mp4")}
    for key, stem in names.items():
        mp4 = got.get(key)
        if not mp4:
            rep.fail(stem, f"file {key}.mp4 non scaricato")
            continue
        dst = lay.video / f"{stem}.mp4"
        shutil.move(str(mp4), dst)
        manifest.videos[key] = stem
        if plan[key].get("argomento"):
            manifest.meta(stem)["argomento"] = plan[key]["argomento"]
        manifest.save()
        rep.done.append(stem)
        rep.outputs.append(str(dst))
    shutil.rmtree(dl_dir, ignore_errors=True)
    return rep
