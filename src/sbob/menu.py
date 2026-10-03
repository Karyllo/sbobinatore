"""Menu guidato: scegli corso → vedi lo stato → spunta i passi (pre-selezionati quelli con lavoro in sospeso) → esegui.

Usa le stesse funzioni dei comandi (`cli._run_step`), quindi menu e CLI non possono divergere.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from sbob.config import ConfigError, Course, load_settings
from sbob.core.report import Exit
from sbob.core.status import course_status

console = Console(stderr=True)
STEP_LABELS = {
    "materiale": "Aggiorna il materiale (WeBeep, siti dei docenti)",
    "download": "Scarica le registrazioni",
    "audio": "Estrai l'audio dai video",
    "trascrivi": "Trascrivi gli audio",
    "appunti": "Genera gli appunti",
}


def suggest_steps(status: dict[str, Any], course: Course) -> dict[str, bool]:
    """Quali passi pre-selezionare: quelli per cui ci sono file pronti da elaborare."""
    todo = status["da_fare"]
    return {
        "materiale": bool(course.webeep_id or course.extra.get("materiale_siti")),
        "download": bool(course.sorgenti) and status["totali"]["video"] == 0,
        "audio": bool(todo["audio"]),
        "trascrivi": bool(todo["trascrizione"]),
        "appunti": bool(todo["appunti"]),
    }


def summary(status: dict[str, Any]) -> str:
    t = status["totali"]
    return (f"{t['lezioni']} lezioni · video {t['video']} · audio {t['audio']} · "
            f"trascrizioni {t['trascrizione']} · appunti {t['appunti']}")


HOME_CHOICES: list[tuple[str, Any]] = [
    ("Lavorare su un corso (scarica, trascrivi, appunti…)", "corso"),
    ("Aggiornare tutti i miei corsi in un colpo solo", ["aggiorna"]),
    ("Scegliere quali corsi seguire (da WeBeep)", ["webeep", "scegli"]),
    ("Cercare un argomento negli appunti", "cerca"),
    ("Fare una domanda al taccuino NotebookLM", "domanda"),
    ("Accedere o rinnovare l'accesso (Poli, WeBeep, Webex)", ["login"]),
    ("Controllare che sia tutto a posto", ["doctor"]),
    ("Come funziona (aiuto)", ["aiuto"]),
    ("Esci", None),
]


def _spawn(*args: str) -> int:
    """Lancia un comando sbob nel terminale dell'utente (stesso comportamento della riga di comando: il menu non diverge)."""
    import subprocess

    from sbob.pianifica import sbob_binary

    return subprocess.call([sbob_binary(), *args])


def _pick_course(questionary, settings, message: str) -> str | None:
    if len(settings.corsi) == 1:
        return next(iter(settings.corsi))
    return questionary.select(message, choices=[questionary.Choice(c.nome, value=s) for s, c in settings.corsi.items()]).ask()


def menu() -> int:
    import questionary

    try:
        settings = load_settings()
    except (ConfigError, OSError) as e:
        console.print(f"[red]Config:[/red] {e}")
        return int(Exit.ERROR)
    if not settings.corsi:                                 # primo avvio: niente comandi da ricordare, si parte da qui
        console.print("[bold]Benvenuto in sbob![/bold] Non c'è ancora nessun corso configurato.")
        if questionary.confirm("Faccio la configurazione adesso? Ti faccio qualche domanda, ci vogliono pochi minuti.",
                               default=True).ask():
            _spawn("init")
            settings = load_settings()
        if not settings.corsi:
            console.print("Quando vuoi: [bold]sbob init[/bold] per configurare, poi [bold]sbob webeep scegli[/bold] "
                          "per scegliere i corsi da WeBeep. Per una spiegazione: [bold]sbob aiuto[/bold].")
            return int(Exit.OK)

    code = Exit.OK
    while True:
        action = questionary.select("Cosa vuoi fare?", choices=[questionary.Choice(label, value=i)
                                                                for i, (label, _) in enumerate(HOME_CHOICES)]).ask()
        if action is None or HOME_CHOICES[action][1] is None:
            return int(code)
        target = HOME_CHOICES[action][1]
        if target == "corso":
            code = Exit(max(code, _work_on_course(settings)))
        elif target == "cerca":
            term = questionary.text("Cosa cerchi? (una o più parole)").ask()
            if term and term.strip():
                _spawn("cerca", term.strip())
        elif target == "domanda":
            slug = _pick_course(questionary, settings, "Su quale corso?")
            question = questionary.text("La tua domanda:").ask() if slug else None
            if slug and question and question.strip():
                _spawn("notebook", slug, "chiedi", question.strip())
        else:
            _spawn(*target)
        questionary.press_any_key_to_continue("Premi un tasto per tornare al menu…").ask()


def _work_on_course(settings) -> int:
    import questionary

    from sbob.cli import _print_report, _run_step

    statuses = {slug: course_status(c) for slug, c in settings.corsi.items()}
    slug = questionary.select(
        "Quale corso?",
        choices=[questionary.Choice(f"{c.nome} — {summary(statuses[s])}", value=s) for s, c in settings.corsi.items()],
    ).ask()
    if slug is None:
        return 0
    course, status = settings.corsi[slug], statuses[slug]
    console.print(f"[bold]{course.nome}[/bold]: {summary(status)}")

    edition = None
    if course.archivio:                                  # "lavora sull'archivio dell'anno precedente"
        from sbob.core.archivio import archive_course

        choice = questionary.select("Su quale edizione?", choices=[
            questionary.Choice(f"Anno in corso ({course.anno_accademico})", value=None),
            *[questionary.Choice(f"Archivio {y}", value=y) for y in sorted(course.archivio, reverse=True)]]).ask()
        if choice:
            edition = choice
            course = archive_course(course, choice)
            status = course_status(course)
            console.print(f"[bold]Archivio {choice}[/bold]: {summary(status)}")
    suggested = suggest_steps(status, course)
    steps = questionary.checkbox(
        "Cosa vuoi fare?",
        choices=[questionary.Choice(label, value=s, checked=suggested[s]) for s, label in STEP_LABELS.items()],
    ).ask()
    if not steps:
        console.print("Niente da fare.")
        return 0

    mode = questionary.select("Come?", choices=[
        questionary.Choice("Esegui", value="run"),
        questionary.Choice("Prima mostra cosa farebbe (dry-run)", value="dry"),
        questionary.Choice("Annulla", value="no")]).ask()
    if mode in (None, "no"):
        return 0

    code = Exit.OK
    for step in steps:
        if step == "appunti" and mode == "run":      # le chiamate LLM costano: mostra la stima e chiedi conferma
            est = _run_step(step, slug, dry_run=True, archivio=edition)
            _print_report(est)
            if est.done and not questionary.confirm("Procedo con la generazione degli appunti?", default=True).ask():
                continue
        rep = _run_step(step, slug, dry_run=(mode == "dry"), archivio=edition)
        _print_report(rep)
        code = max(code, rep.exit_code)
        if rep.exit_code in (Exit.HUMAN, Exit.ERROR):
            console.print("[yellow]Mi fermo qui.[/yellow]")
            break
    return int(code)
