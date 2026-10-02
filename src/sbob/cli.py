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
    if r.cost.get("usd_totale"):
        err.print(f"  costo stimato: ${r.cost['usd_totale']:.4f}")
    if r.needs_human:
        err.print(f"  [yellow]⚠ serve un tuo intervento:[/yellow] {r.needs_human}")
        if r.action:
            err.print(f"    → {r.action}")
    if r.error:
        err.print(f"  [red]errore:[/red] {r.error}")


def _run_step(step: str, corso: str, *, force=False, dry_run=False, only=None, options=None,
              quiet=False) -> StepReport:
    settings = _settings()
    try:
        course = settings.corso(corso)
    except ConfigError as e:
        return StepReport(step=step, corso=corso, error=str(e))
    ctx = StepContext(settings, course, force=force, dry_run=dry_run,
                      only=set(only) if only else None, options=options or {}, quiet=quiet)
    try:
        with _stdout_guard(quiet):
            return get_step(step)(ctx)
    except NeedsHuman as e:
        return StepReport(step=step, corso=corso, needs_human=str(e), action=e.action)
    except (ModuleNotFoundError, NotImplementedError) as e:
        return StepReport(step=step, corso=corso, error=f"passo non disponibile: {e}")
    except Exception as e:  # noqa: BLE001 — il report deve sempre uscire, anche su bug
        return StepReport(step=step, corso=corso, error=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- info


@app.command()
def corsi(as_json: JsonOpt = False):
    """Elenca i corsi configurati."""
    s = _settings()
    rows = [{"slug": c.slug, "nome": c.nome, "anno": c.anno_accademico, "cartella": str(c.cartella),
             "esiste": c.cartella.exists(), "trascrizione": c.trascrizione,
             "sorgente": c.sorgente.get("tipo")} for c in s.corsi.values()]
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
    data = [course_status(c) for c in targets]
    if as_json:
        sys.stdout.write(json.dumps(data[0] if corso else data, ensure_ascii=False, indent=2) + "\n")
        return
    for d in data:
        tot = d["totali"]
        out.print(f"[bold]{d['nome']}[/bold] ({d['corso']}) — {tot['lezioni']} lezioni · "
                  f"video {tot['video']} · audio {tot['audio']} · trascr. {tot['trascrizione']} · appunti {tot['appunti']}")
        t = Table("lezione", "video", "audio", "trascr.", "appunti", "prossimo")
        tick = lambda b: "[green]✓[/green]" if b else "·"  # noqa: E731
        for l in d["lezioni"]:
            t.add_row(l["lezione"] if l["nome_valido"] else f"[yellow]{l['lezione']}[/yellow]",
                      tick(l["video"]), tick(l["audio"]), tick(l["trascrizione"]), tick(l["appunti"]),
                      l["prossimo_passo"] or "")
        out.print(t)


# --------------------------------------------------------------------------- passi


@app.command()
def download(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False,
             tipo: Annotated[Optional[str], typer.Option(help="Forza il tipo: lez|ese|lab|sem|tde (default: dalla forma didattica dell'archivio, altrimenti lez)")] = None,
             links: Annotated[Optional[str], typer.Option(help="File di link alternativo a quello del corso.")] = None):
    """Scarica le registrazioni del corso in video/."""
    _emit([_run_step("download", corso, force=force, dry_run=dry_run, quiet=as_json,
                     options={"tipo": tipo, "links": links})], as_json)


@app.command()
def audio(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
          as_json: JsonOpt = False):
    """Estrae l'audio (.aac) dai video."""
    _emit([_run_step("audio", corso, force=force, dry_run=dry_run, only=only, quiet=as_json)], as_json)


@app.command()
def trascrivi(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
              as_json: JsonOpt = False,
              backend: Annotated[Optional[str], typer.Option(help="gemini|notebooklm|html (whisper: non mantenuto)")] = None,
              html_dir: Annotated[Optional[str], typer.Option("--html-dir", help="Per backend html: cartella export.")] = None):
    """Trascrive gli audio in trascrizioni/."""
    _emit([_run_step("trascrivi", corso, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"backend": backend, "html_dir": html_dir})], as_json)


@app.command()
def appunti(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
            as_json: JsonOpt = False,
            refiner: Annotated[Optional[str], typer.Option(help="provider[:modello], es. deepseek")] = None,
            notes: Annotated[Optional[str], typer.Option(help="provider[:modello], es. anthropic:claude-sonnet-5-5")] = None):
    """Genera le dispense dalle trascrizioni."""
    _emit([_run_step("appunti", corso, force=force, dry_run=dry_run, only=only, quiet=as_json,
                     options={"refiner": refiner, "notes": notes})], as_json)


@app.command()
def merge(corso: str, as_json: JsonOpt = False,
          modo: Annotated[str, typer.Option(help="split|monolite|tde")] = "monolite",
          da: Annotated[str, typer.Option(help="appunti|trascrizioni|materiale")] = "appunti"):
    """Unisce i file del corso in merge/ (per NotebookLM o un LLM)."""
    _emit([_run_step("merge", corso, quiet=as_json, options={"modo": modo, "da": da})], as_json)


@app.command()
def mappa(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, only: OnlyOpt = None,
          as_json: JsonOpt = False,
          modello: Annotated[Optional[str], typer.Option(help="provider[:modello] per il ruolo mappa")] = None):
    """Schede per l'agente (riassunto, concetti, prerequisiti) in <corso>/mappa/, poi rigenera gli indici."""
    _emit([_run_step("mappa", corso, force=force, dry_run=dry_run, only=only, quiet=as_json,
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
          as_json: JsonOpt = False):
    """Cerca nei corsi: restituisce lezione, sezione, minuto e il paragrafo giusto."""
    from sbob.core.search import SOURCES, search

    bad = [d for d in dove or [] if d not in SOURCES]
    if bad:
        err.print(f"[red]--in non valido: {bad} (validi: {', '.join(SOURCES)})[/red]")
        raise typer.Exit(Exit.ERROR)
    res = search(_settings(), query, corso, tuple(dove) if dove else SOURCES, limite)
    if as_json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return
    err.print(f"{res['totale']} risultati per {query!r}" + (f" (mostro {limite})" if res["totale"] > limite else ""))
    for h in res["risultati"]:
        where = " · ".join(x for x in (h["corso"], h["lezione"], h["fonte"], h.get("sezione"),
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
        out: Annotated[Optional[str], typer.Option("--out", help="Cartella di destinazione (default ./markdown_output senza corso).")] = None,
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
def run(corso: str, force: ForceOpt = False, dry_run: DryOpt = False, as_json: JsonOpt = False,
        da: Annotated[str, typer.Option("--da", help="Primo passo.")] = PIPELINE[0],
        fino_a: Annotated[str, typer.Option("--fino-a", help="Ultimo passo.")] = PIPELINE[-1]):
    """Esegue la catena download → audio → trascrivi → appunti → mappa. Si ferma se un passo richiede l'utente."""
    try:
        steps = PIPELINE[PIPELINE.index(da): PIPELINE.index(fino_a) + 1]
    except ValueError:
        err.print(f"[red]Passi validi: {', '.join(PIPELINE)}[/red]")
        raise typer.Exit(Exit.ERROR)
    reports, pending = [], set()
    for step in steps:
        r = _run_step(step, corso, force=force, dry_run=dry_run, quiet=as_json)
        if dry_run and pending:
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
    if as_json:
        _emit(reports, True)
    raise typer.Exit(int(max(r.exit_code for r in reports)))


# --------------------------------------------------------------------------- cookie


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
