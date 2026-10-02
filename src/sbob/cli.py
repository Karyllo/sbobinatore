"""CLI `sbob`. Senza argomenti apre il menu guidato.

Convenzioni (valgono per tutti i comandi, servono soprattutto agli agenti):
  --json   un solo oggetto JSON su stdout; log e progressi su stderr
  exit     0 ok · 1 errore · 2 parziale · 3 serve l'utente (vedi core.report)
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from sbob.config import ConfigError, Settings, load_settings
from sbob.core.report import Exit, NeedsHuman, StepReport
from sbob.steps.base import PIPELINE, StepContext, get_step

app = typer.Typer(add_completion=False, no_args_is_help=False, rich_markup_mode="rich",
                  help="Pipeline lezioni: download → audio → trascrivi → appunti (+ merge, pdf).")
out = Console()
err = Console(stderr=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Output JSON su stdout (per agenti/script).")]
ForceOpt = Annotated[bool, typer.Option("--force", help="Rifai anche ciò che esiste già.")]
DryOpt = Annotated[bool, typer.Option("--dry-run", help="Mostra cosa farebbe senza eseguire.")]
OnlyOpt = Annotated[Optional[list[str]], typer.Option("--solo", help="Limita a queste lezioni (stem). Ripetibile.")]
ArchOpt = Annotated[Optional[str], typer.Option("--archivio", help="Lavora sull'edizione passata di quell'anno (es. 2024-25), o `scegli` per sceglierla da un elenco.")]


def _stdout_guard(as_json: bool):
    """Con --json, qualsiasi print delle librerie (avvisi, progressi) va su stderr: stdout resta JSON puro."""
    return contextlib.redirect_stdout(sys.stderr) if as_json else contextlib.nullcontext()


def _settings() -> Settings:
    try:
        return load_settings()
    except (ConfigError, OSError) as e:
        err.print(f"[red]Config:[/red] {e}")
        raise typer.Exit(Exit.ERROR)


def _emit(reports: list[StepReport], as_json: bool) -> None:
    code = max((r.exit_code for r in reports), default=Exit.OK)
    if as_json:
        payload: Any = reports[0].to_dict() if len(reports) == 1 else {
            "reports": [r.to_dict() for r in reports], "exit_code": int(code)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        for r in reports:
            _print_report(r)
    raise typer.Exit(int(code))


def _print_report(r: StepReport) -> None:
    head = f"[bold]{r.step}[/bold]" + (f" · {r.corso}" if r.corso else "") + (" [dim](dry-run)[/dim]" if r.dry_run else "")
    verb = "da fare" if r.dry_run else "fatti"
    err.print(f"{head}: {len(r.done)} {verb}, {len(r.skipped)} già fatti, {len(r.failed)} falliti")
    for f in r.failed:
        err.print(f"  [red]✗[/red] {f.item}: {f.error}")
    for n in r.notes:
        err.print(f"  [dim]{n}[/dim]")
    for w in r.warnings:
        err.print(f"  [yellow]⚠ {w}[/yellow]")
    if r.cost.get("usd_totale"):
        err.print(f"  costo stimato: ${r.cost['usd_totale']:.4f}")
    if r.needs_human:
        err.print(f"  [yellow]⚠ serve un tuo intervento:[/yellow] {r.needs_human}")
        if r.action:
            err.print(f"    → {r.action}")
    if r.error:
        err.print(f"  [red]errore:[/red] {r.error}")


def _resolve_course(settings: Settings, corso: str, archivio: str | None):
    """Il corso, oppure la sua edizione passata (`--archivio <anno>`, o `scegli` per un elenco)."""
    from sbob.core.archivio import archive_course

    course = settings.corso(corso)
    if not archivio:
        return course
    if archivio.lower() in ("scegli", "?"):
        years = sorted(course.archivio, reverse=True)
        if not years:
            raise ConfigError(f"{corso} non ha edizioni passate: sbob archivio {corso} aggiungi")
        if not sys.stdin.isatty():
            raise ConfigError(f"Scegli l'anno con --archivio <anno> (configurati: {', '.join(years)})")
        import questionary
        archivio = questionary.select("Quale edizione?", choices=years).ask()
        if not archivio:
            raise ConfigError("Nessuna edizione scelta")
    return archive_course(course, archivio)


def _run_step(step: str, corso: str, *, force=False, dry_run=False, only=None, options=None,
              quiet=False, archivio: str | None = None) -> StepReport:
    settings = _settings()
    try:
        course = _resolve_course(settings, corso, archivio)
    except ConfigError as e:
        return StepReport(step=step, corso=corso, error=str(e))
    ctx = StepContext(settings, course, force=force, dry_run=dry_run,
                      only=set(only) if only else None, options=options or {}, quiet=quiet, step=step)
    try:
        with _stdout_guard(quiet):
            return get_step(step)(ctx)
    except NeedsHuman as e:
        return StepReport(step=step, corso=corso, needs_human=str(e), action=e.action)
    except (ModuleNotFoundError, NotImplementedError) as e:
        return StepReport(step=step, corso=corso, error=f"passo non disponibile: {e}")
    except Exception as e:  # noqa: BLE001 — il report deve sempre uscire, anche su bug
        ctx.log_exception()
        return StepReport(step=step, corso=corso, error=f"{type(e).__name__}: {e} (dettagli in {ctx.layout.logs / 'sbob.log'})")


# --------------------------------------------------------------------------- info


@app.command()
def corsi(as_json: JsonOpt = False):
    """Elenca i corsi configurati."""
    s = _settings()
    rows = [{"slug": c.slug, "nome": c.nome, "anno": c.anno_accademico, "cartella": str(c.cartella),
             "esiste": c.cartella.exists(), "trascrizione": c.trascrizione,
             "sorgente": ", ".join(s["tipo"] for s in c.sorgenti) or None} for c in s.corsi.values()]
    if as_json:
        sys.stdout.write(json.dumps({"config": str(s.path) if s.path else None, "root": str(s.root),
                                     "corsi": rows}, ensure_ascii=False, indent=2) + "\n")
        return
    if not s.path:
        err.print("[yellow]Nessun sbob.toml trovato[/yellow] (cerca: $SBOB_CONFIG, ./sbob.toml, ~/.config/sbob/sbob.toml)")
    t = Table("slug", "nome", "anno", "trascrizione", "cartella")
    for r in rows:
        t.add_row(r["slug"], r["nome"], r["anno"], r["trascrizione"],
                  r["cartella"] if r["esiste"] else f"[red]{r['cartella']} (manca)[/red]")
    out.print(t)


@app.command()
def status(corso: Annotated[Optional[str], typer.Argument(help="Slug del corso (vuoto = tutti).")] = None,
           as_json: JsonOpt = False):
    """Per ogni lezione: quali passi sono fatti e qual è il prossimo."""
    from sbob.core.status import course_status

    s = _settings()
    try:
        targets = [s.corso(corso)] if corso else list(s.corsi.values())
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(Exit.ERROR)
    from sbob.core.archivio import archives_of

    data = []
    for c in targets:
        d = course_status(c)
        d["archivio"] = {a.anno_accademico: course_status(a) for a in archives_of(c)}      # edizioni passate
        data.append(d)
    if as_json:
        sys.stdout.write(json.dumps(data[0] if corso else data, ensure_ascii=False, indent=2) + "\n")
        return
    flat = []
    for d in data:
        flat.append(d)
        flat += [{**a, "nome": f"{d['nome']} — edizione {anno}"} for anno, a in d["archivio"].items()]
    for d in flat:
        tot = d["totali"]
        mat = d["materiale"]
        out.print(f"[bold]{d['nome']}[/bold] ({d['corso']}) — {tot['lezioni']} lezioni · "
                  f"video {tot['video']} · audio {tot['audio']} · trascr. {tot['trascrizione']} · appunti {tot['appunti']}"
                  + (f" · materiale {mat['convertiti']}/{mat['file']}"
                     + (f" ({mat['solo_testo']} solo testo)" if mat["solo_testo"] else "") if mat["file"] else ""))
        t = Table("lezione", "video", "audio", "trascr.", "appunti", "prossimo")
        tick = lambda b: "[green]✓[/green]" if b else "·"  # noqa: E731
        for l in d["lezioni"]:
            t.add_row(l["lezione"] if l["nome_valido"] else f"[yellow]{l['lezione']}[/yellow]",
                      tick(l["video"]), tick(l["audio"]), tick(l["trascrizione"]), tick(l["appunti"]),
                      l["prossimo_passo"] or "")
        out.print(t)


# --------------------------------------------------------------------------- passi


@app.command()
def download(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False,
             tipo: Annotated[Optional[str], typer.Option(help="Forza il tipo: lez|ese|lab|sem|tde (default: dalla forma didattica dell'archivio, altrimenti lez)")] = None,
             links: Annotated[Optional[str], typer.Option(help="File di link alternativo a quello del corso.")] = None):
    """Scarica le registrazioni del corso in video/."""
    _emit([_run_step("download", corso, archivio=archivio, force=force, dry_run=dry_run, quiet=as_json,
                     options={"tipo": tipo, "links": links})], as_json)


@app.command()
def audio(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
          as_json: JsonOpt = False):
    """Estrae l'audio (.aac) dai video."""
    _emit([_run_step("audio", corso, archivio=archivio, force=force, dry_run=dry_run, only=only, quiet=as_json)], as_json)


@app.command()
def trascrivi(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
              as_json: JsonOpt = False,
              backend: Annotated[Optional[str], typer.Option(help="gemini|notebooklm|html (whisper: non mantenuto)")] = None,
              html_dir: Annotated[Optional[str], typer.Option("--html-dir", help="Per backend html: cartella export.")] = None):
    """Trascrive gli audio in trascrizioni/."""
    _emit([_run_step("trascrivi", corso, archivio=archivio, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"backend": backend, "html_dir": html_dir})], as_json)


@app.command()
def appunti(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
            as_json: JsonOpt = False,
            refiner: Annotated[Optional[str], typer.Option(help="provider[:modello], es. deepseek")] = None,
            notes: Annotated[Optional[str], typer.Option(help="provider[:modello], es. anthropic:claude-sonnet-5-5")] = None):
    """Genera le dispense dalle trascrizioni."""
    _emit([_run_step("appunti", corso, archivio=archivio, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"refiner": refiner, "notes": notes})], as_json)


@app.command()
def merge(corso: str, archivio: ArchOpt = None, as_json: JsonOpt = False,
          modo: Annotated[str, typer.Option(help="split|monolite|tde")] = "monolite",
          da: Annotated[str, typer.Option(help="appunti|trascrizioni|materiale")] = "appunti"):
    """Unisce i file del corso in merge/ (per NotebookLM o un LLM)."""
    _emit([_run_step("merge", corso, archivio=archivio, quiet=as_json, options={"modo": modo, "da": da})], as_json)


@app.command()
def link(corso: str, archivio: ArchOpt = None, as_json: JsonOpt = False,
         url: Annotated[Optional[str], typer.Option(help="Link all'archivio (modulo WeBeep o getservizio); default: dal corso WeBeep collegato.")] = None):
    """Raccoglie i link delle registrazioni dall'archivio del Poli e li scrive in <corso>/link_archivio.txt (nessun download)."""
    from sbob.steps.download import archive_links

    s = _settings()
    try:
        course = _resolve_course(s, corso, archivio)
        ctx = StepContext(s, course, quiet=as_json, step="link")
        course.cartella.mkdir(parents=True, exist_ok=True)
        with _stdout_guard(as_json):
            path = archive_links(ctx, {"tipo": "archivio", **({"url": url} if url else {})})
        n = sum(1 for l in path.read_text().splitlines() if l.strip() and not l.startswith("#"))
        rep = StepReport(step="link", corso=corso, done=[f"{n} registrazioni"], outputs=[str(path)])
    except NeedsHuman as e:
        rep = StepReport(step="link", corso=corso, needs_human=str(e), action=e.action)
    except (ConfigError, RuntimeError) as e:
        rep = StepReport(step="link", corso=corso, error=str(e))
    _emit([rep], as_json)


@app.command()
def materiale(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
              as_json: JsonOpt = False,
              modello: Annotated[Optional[str], typer.Option(help="provider[:modello] per la conversione dei PDF")] = None,
              converti: Annotated[Optional[bool], typer.Option("--converti/--senza-conversione",
                  help="Converte in Markdown (default: [materiale] converti in sbob.toml, altrimenti sì).")] = None):
    """Scarica il materiale da WeBeep (nuovo o modificato) e lo converte in Markdown in <corso>/materiale_md/."""
    _emit([_run_step("materiale", corso, archivio=archivio, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"modello": modello, "converti": converti})], as_json)


webeep_app = typer.Typer(help="Corsi e materiale su WeBeep (serve `sbob login`).", no_args_is_help=True)
app.add_typer(webeep_app, name="webeep")


@webeep_app.command("corsi")
def webeep_corsi(as_json: JsonOpt = False):
    """Elenca i corsi WeBeep (anche degli anni passati) e quali sono già collegati a un corso di sbob."""
    from sbob.auth.browser import load_token
    from sbob.webeep.client import WebeepClient

    s = _settings()
    try:
        courses = WebeepClient(load_token() or "").courses()
    except NeedsHuman as e:
        _emit([StepReport(step="webeep", needs_human=str(e), action=e.action)], as_json)
        return
    linked = {c.webeep_id: slug for slug, c in s.corsi.items() if c.webeep_id}
    for c in courses:
        c["collegato_a"] = linked.get(c["id"])
    if as_json:
        sys.stdout.write(json.dumps({"corsi": courses}, ensure_ascii=False, indent=2) + "\n")
        return
    t = Table("id", "anno", "corso", "collegato a")
    for c in courses:
        t.add_row(str(c["id"]), c["anno"] or "", c["nome"][:70], c["collegato_a"] or "")
    out.print(t)


@webeep_app.command("scegli")
def webeep_scegli(as_json: JsonOpt = False,
                  id: Annotated[Optional[list[int]], typer.Option("--id", help="Da script: id WeBeep da aggiungere (ripetibile).")] = None,
                  tutti_gli_anni: Annotated[bool, typer.Option("--tutti-gli-anni", help="Mostra anche i corsi degli anni passati.")] = False):
    """Scegli con un elenco a spunte i corsi WeBeep da sincronizzare: crea quelli nuovi in sbob.toml (i tolti restano)."""
    from sbob.scelta import run_scegli

    _emit([run_scegli(_settings(), id or None, tutti_gli_anni, as_json)], as_json)


@webeep_app.command("collega")
def webeep_collega(corso: str, webeep_id: int):
    """Collega un corso di sbob a un corso WeBeep (scrive webeep_id in sbob.toml)."""
    from sbob.setup import set_course_field

    s = _settings()
    try:
        s.corso(corso)
        set_course_field(s.path, corso, "webeep_id", webeep_id)
    except (ConfigError, ValueError) as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(Exit.ERROR)
    err.print(f"[green]{corso} ↔ WeBeep {webeep_id}.[/green] Ora: sbob materiale {corso} --dry-run")


@app.command()
def mappa(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
          as_json: JsonOpt = False,
          modello: Annotated[Optional[str], typer.Option(help="provider[:modello] per il ruolo mappa")] = None):
    """Schede per l'agente (riassunto, concetti, prerequisiti) in <corso>/mappa/, poi rigenera gli indici."""
    _emit([_run_step("mappa", corso, archivio=archivio, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"modello": modello})], as_json)


@app.command()
def indice(corso: Annotated[Optional[list[str]], typer.Argument(help="Corsi (vuoto = tutti + indice globale).")] = None,
           as_json: JsonOpt = False):
    """Rigenera mappe e indici dai file, senza chiamate LLM."""
    from sbob.core.index import render_all

    s = _settings()
    data = render_all(s, corso or None)
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return
    for c in data["corsi"]:
        n = sum(1 for l in c["lezioni"] if l["riassunto"])
        err.print(f"{c['nome']}: {len(c['lezioni'])} lezioni ({n} con scheda), {len(c['concetti'])} concetti → {c['indice']}")
    if data["indice_globale"]:
        err.print(f"indice globale → {data['indice_globale']}")


@app.command()
def cerca(query: Annotated[str, typer.Argument(help='Termini (tutti devono comparire); "frase tra virgolette".')],
          corso: Annotated[Optional[list[str]], typer.Option("--corso", help="Limita a questi corsi. Ripetibile.")] = None,
          dove: Annotated[Optional[list[str]], typer.Option("--in", help="mappa|appunti|trascrizioni|materiale. Ripetibile.")] = None,
          limite: Annotated[int, typer.Option(help="Numero massimo di risultati.")] = 20,
          archivi: Annotated[Optional[bool], typer.Option("--archivi/--senza-archivi",
              help="Include le edizioni passate. Default: solo l'anno in corso, e se non trova niente anche le passate.")] = None,
          as_json: JsonOpt = False):
    """Cerca nei corsi: restituisce lezione, sezione, minuto e il paragrafo giusto."""
    from sbob.core.search import SOURCES, search

    bad = [d for d in dove or [] if d not in SOURCES]
    if bad:
        err.print(f"[red]--in non valido: {bad} (validi: {', '.join(SOURCES)})[/red]")
        raise typer.Exit(Exit.ERROR)
    settings_ = _settings()
    s_years = {k: c.anno_accademico for k, c in settings_.corsi.items()}
    res = search(settings_, query, corso, tuple(dove) if dove else SOURCES, limite,
                 archivi={None: "auto", True: "si", False: "no"}[archivi])
    if as_json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return
    err.print(f"{res['totale']} risultati per {query!r}" + (f" (mostro {limite})" if res["totale"] > limite else ""))
    if res.get("nota"):
        err.print(f"[yellow]{res['nota']}[/yellow]")
    for h in res["risultati"]:
        where = " · ".join(x for x in (h["corso"], h["lezione"], h["fonte"], h.get("sezione"),
                                        f"edizione {h['edizione']}" if h["edizione"] != s_years.get(h["corso"]) else None,
                                        f"min {h['minuto']}" if h.get("minuto") else None) if x)
        out.print(f"[bold]{where}[/bold]\n  {h['testo']}\n  [dim]{h['file']}[/dim]")


@app.command()
def verifica(corso: Annotated[Optional[str], typer.Argument(help="Slug del corso (vuoto = tutti).")] = None,
             as_json: JsonOpt = False,
             senza_audio: Annotated[bool, typer.Option("--senza-audio", help="Salta il controllo durata audio (più veloce).")] = False):
    """Controllo qualità: contenuto perso, trascrizioni troncate, loop, parziali, numerazione, mappa vecchia."""
    from sbob.core.verify import verify_course

    s = _settings()
    try:
        targets = [s.corso(corso)] if corso else list(s.corsi.values())
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(Exit.ERROR)
    data = [verify_course(c, check_audio=not senza_audio) for c in targets if c.cartella.exists()]
    if as_json:
        sys.stdout.write(json.dumps(data[0] if corso and data else data, ensure_ascii=False, indent=2) + "\n")
    else:
        colors = {"errore": "red", "avviso": "yellow", "info": "dim"}
        for d in data:
            t = d["totali"]
            out.print(f"[bold]{d['corso']}[/bold]: {t['errore']} errori, {t['avviso']} avvisi, {t['info']} info")
            for i in d["problemi"]:
                c = colors[i["livello"]]
                out.print(f"  [{c}]{i['livello']}[/{c}] {i['lezione'] or ''} {i['problema']}"
                          + (f"\n    → {i['suggerimento']}" if i["suggerimento"] else ""))
    raise typer.Exit(int(Exit.PARTIAL if any(d["totali"]["errore"] for d in data) else Exit.OK))


@app.command()
def pdf(path: Annotated[str, typer.Argument(help="File .pdf o cartella di PDF.")],
        corso: Annotated[Optional[str], typer.Option("--corso", help="Corso: l'output va in <corso>/.sbob/md.")] = None,
        out: Annotated[Optional[str], typer.Option("--out", help="Cartella di destinazione (default: <corso>/materiale_md, o ./markdown_output senza corso).")] = None,
        force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False,
        modello: Annotated[Optional[str], typer.Option(help="provider[:modello] per il ruolo pdf")] = None):
    """Converte PDF in Markdown con un modello multimodale (formule in LaTeX, immagini descritte)."""
    from sbob.config import Course
    from pathlib import Path

    settings = _settings()
    if corso:
        try:
            course = settings.corso(corso)
        except ConfigError as e:
            _emit([StepReport(step="pdf", corso=corso, error=str(e))], as_json)
            return
    else:
        dest = Path(out or "markdown_output").expanduser().resolve()
        course = Course(slug="pdf", nome="pdf", anno_accademico="", cartella=dest, lingua=settings.lingua)
        out = out or str(dest)
    ctx = StepContext(settings, course, force=force, dry_run=dry_run, quiet=as_json,
                      options={"path": path, "out": out, "modello": modello})
    try:
        with _stdout_guard(as_json):
            rep = get_step("pdf")(ctx)
    except NeedsHuman as e:
        rep = StepReport(step="pdf", corso=course.slug, needs_human=str(e), action=e.action)
    except ModuleNotFoundError as e:
        rep = StepReport(step="pdf", corso=course.slug, error=f"dipendenza mancante: {e} (uv sync --extra pdf)")
    _emit([rep], as_json)


@app.command()
def run(corso: str, archivio: ArchOpt = None, force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False,
        da: Annotated[str, typer.Option("--da", help="Primo passo.")] = PIPELINE[0],
        fino_a: Annotated[str, typer.Option("--fino-a", help="Ultimo passo.")] = PIPELINE[-1]):
    """Esegue la catena materiale → download → audio → trascrivi → appunti → mappa → notebook. Si ferma se un passo richiede l'utente."""
    try:
        steps = PIPELINE[PIPELINE.index(da): PIPELINE.index(fino_a) + 1]
    except ValueError:
        err.print(f"[red]Passi validi: {', '.join(PIPELINE)}[/red]")
        raise typer.Exit(Exit.ERROR)
    reports = _run_chain(corso, steps, archivio=archivio, force=force, dry_run=dry_run, as_json=as_json)
    if as_json:
        _emit(reports, True)
    raise typer.Exit(int(max(r.exit_code for r in reports)))


def _run_chain(corso: str, steps, *, archivio=None, force=False, dry_run=False, as_json=False) -> list[StepReport]:
    reports, pending = [], set()
    for step in steps:
        r = _run_step(step, corso, archivio=archivio, force=force, dry_run=dry_run, quiet=as_json)
        if dry_run and pending and step != "notebook":     # il taccuino lavora per sorgente, non per lezione
            # in simulazione i passi precedenti non hanno prodotto file: aggiungo ciò che produrrebbero
            extra = pending - set(r.done) - set(r.skipped)
            if extra:
                r.done = sorted(set(r.done) | extra)
                r.notes.append(f"{len(extra)} in arrivo dai passi precedenti (stime di costo escluse)")
        pending = set(r.done)
        reports.append(r)
        if not as_json:
            _print_report(r)
        if r.exit_code in (Exit.HUMAN, Exit.ERROR):
            break
    return reports


@app.command()
def aggiorna(force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False):
    """Esegue la catena completa su tutti i corsi collegati a WeBeep (quelli scelti con `sbob webeep scegli`)."""
    s = _settings()
    slugs = [slug for slug, c in s.corsi.items() if c.webeep_id]
    if not slugs:
        _emit([StepReport(step="aggiorna", error="Nessun corso collegato a WeBeep: sbob webeep scegli")], as_json)
        return
    reports: list[StepReport] = []
    for slug in slugs:
        if not as_json:
            err.print(f"[bold]── {slug}[/bold]")
        rs = _run_chain(slug, PIPELINE, force=force, dry_run=dry_run, as_json=as_json)
        reports += rs
        if any(r.exit_code == Exit.HUMAN for r in rs):      # login scaduto & co: inutile insistere sugli altri corsi
            break
    if as_json:
        _emit(reports, True)
    raise typer.Exit(int(max(r.exit_code for r in reports)))


@app.command()
def notebook(corso: str, azione: Annotated[Optional[str], typer.Argument(help="aggiungi-archivio | rimuovi-archivio (vuoto = aggiorna il taccuino)")] = None,
             anno: Annotated[Optional[str], typer.Argument(help="Anno dell'edizione, es. 2024-25.")] = None,
             force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False):
    """Tiene aggiornato il taccuino NotebookLM del corso (appunti e materiale, una sorgente per cartella)."""
    _emit([_run_step("notebook", corso, force=force, dry_run=dry_run, quiet=as_json,
                     options={"azione": azione, "anno": anno, "esplicito": True})], as_json)


# --------------------------------------------------------------------------- cookie


@app.command()
def doctor(as_json: JsonOpt = False,
           veloce: Annotated[bool, typer.Option("--veloce", help="Non avvia il downloader (salta l'installazione).")] = False):
    """Controlla che ci sia tutto (programmi, chiavi, librerie, downloader) e dice come sistemare ciò che manca."""
    from sbob.core.doctor import run_checks

    checks = run_checks(_settings(), quick=veloce)
    missing = [c for c in checks if c["stato"] == "manca"]
    if as_json:
        sys.stdout.write(json.dumps({"ok": not missing, "controlli": checks}, ensure_ascii=False, indent=2) + "\n")
    else:
        icon = {"ok": "[green]✓[/green]", "manca": "[red]✗[/red]", "avviso": "[yellow]![/yellow]"}
        for c in checks:
            out.print(f"{icon[c['stato']]} {c['nome']}: {c['dettaglio']}")
            if c["rimedio"]:
                out.print(f"    → {c['rimedio']}")
        out.print("[green]Tutto pronto.[/green]" if not missing else f"[red]{len(missing)} cose da sistemare.[/red]")
    raise typer.Exit(int(Exit.HUMAN if missing else Exit.OK))


@app.command()
def init(force: Annotated[bool, typer.Option("--force", help="Ricrea la configurazione se esiste già.")] = False):
    """Prima configurazione guidata: cartella dei corsi, modello per gli appunti, chiavi API, primi corsi."""
    from sbob.setup import init as run_init

    if not sys.stdin.isatty():
        err.print("[red]sbob init è interattivo: lancialo da un terminale.[/red]")
        raise typer.Exit(Exit.ERROR)
    raise typer.Exit(run_init(force))


@app.command("aggiungi-corso")
def aggiungi_corso():
    """Aggiunge un corso alla configurazione facendo qualche domanda."""
    from sbob.setup import add_course

    if not sys.stdin.isatty():
        err.print("[red]Comando interattivo: lancialo da un terminale.[/red]")
        raise typer.Exit(Exit.ERROR)
    raise typer.Exit(add_course())


@app.command()
def login(rinnova: Annotated[bool, typer.Option("--rinnova", help="Senza finestra: rinnova token e ticket se la sessione di Ateneo è ancora valida.")] = False,
          as_json: JsonOpt = False):
    """Accesso di Ateneo in una finestra di Chrome: salva token WeBeep e cookie Webex, senza incollare niente."""
    from sbob.auth.browser import login as do_login

    try:
        with _stdout_guard(as_json):
            got = do_login(_settings(), headless=rinnova, log=lambda m: err.print(m))
    except NeedsHuman as e:
        rep = StepReport(step="login", needs_human=str(e), action=e.action)
        _emit([rep], as_json)
        return
    rep = StepReport(step="login", done=[k for k, v in got.items() if v],
                     notes=[f"non ottenuto: {k}" for k, v in got.items() if not v])
    if not got["webeep_token"]:
        rep.error = "token WeBeep non ottenuto"
    _emit([rep], as_json)


@app.command("installa-skill")
def installa_skill(force: Annotated[bool, typer.Option("--force", help="Sovrascrive una skill già presente.")] = False):
    """Installa la skill per Claude Code in ~/.claude/skills/sbobinatore (così Claude sa usare sbob)."""
    import shutil
    from pathlib import Path

    import sbob

    pkg = Path(sbob.__file__).parent
    src = next((p for p in (pkg / "skill", pkg.parents[1] / "skills" / "sbobinatore") if (p / "SKILL.md").exists()), None)
    if src is None:
        err.print("[red]SKILL.md non trovato nel pacchetto.[/red]")
        raise typer.Exit(Exit.ERROR)
    dst = Path.home() / ".claude" / "skills" / "sbobinatore"
    if dst.exists() or dst.is_symlink():
        if dst.resolve() == src.resolve():
            err.print(f"Skill già collegata: {dst} → {src}")
            return
        if not force:
            err.print(f"[yellow]{dst} esiste già.[/yellow] Usa --force per sostituirla.")
            raise typer.Exit(Exit.ERROR)
        shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    err.print(f"[green]Skill installata in {dst}.[/green] Riavvia Claude Code per vederla.")


@app.command("archivio")
def archivio_cmd(corso: str,
                 azione: Annotated[str, typer.Argument(help="aggiungi | docenti | elenco")],
                 anno: Annotated[Optional[str], typer.Argument(help="Anno accademico, es. 2024-25 (per `aggiungi`).")] = None,
                 docente: Annotated[Optional[str], typer.Option("--docente", help="Docente da usare per questa aggiunta (altrimenti: quello del corso).")] = None,
                 id_webeep: Annotated[Optional[int], typer.Option("--id", help="Forza l'id del corso WeBeep di quell'anno (salta il controllo del docente).")] = None,
                 as_json: JsonOpt = False):
    """Edizioni passate dello stesso corso (stesso docente) come sottocartelle: <corso>/archivio/<anno>/.

    aggiungi [anno]: collega le edizioni con lo stesso codice e docente · docenti: scegli il docente · elenco."""
    from sbob.archivio_cmd import run_archivio

    _emit([run_archivio(_settings(), corso, azione, anno, docente, id_webeep, as_json)], as_json)


@app.command()
def cookie(nome: Annotated[str, typer.Argument(help="ticket | MoodleSession | SSL_JSESSIONID")],
           valore: str):
    """Salva un cookie per il downloader (inoltra a `prd set-cookie`)."""
    from sbob.steps.download import set_cookie

    set_cookie(_settings(), nome, valore)
    err.print(f"[green]Cookie {nome} salvato.[/green]")


def main() -> None:
    if len(sys.argv) == 1 and sys.stdin.isatty():
        from sbob.menu import menu
        sys.exit(menu())
    app()


if __name__ == "__main__":
    main()
