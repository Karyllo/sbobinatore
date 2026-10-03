"""Interfaccia comune dei passi.

Un passo è una funzione  run(ctx: StepContext) -> StepReport.
Regole che ogni passo deve rispettare:
  - idempotente: salta ciò che è già fatto (core.batch.plan_jobs) a meno di ctx.force
  - scrive gli output in modo atomico (core.batch.atomic_write_text o rename da file temporaneo)
  - non stampa su stdout: usa ctx.log (va su stderr), così stdout resta libero per --json
  - errori per singolo file → report.fail(...) e prosegue; errori che richiedono l'utente → raise NeedsHuman
  - con ctx.dry_run elenca cosa farebbe in report.done (con report.dry_run=True) senza eseguire
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from sbob.config import Course, Settings
from sbob.core.layout import Layout
from sbob.core.manifest import Manifest
from sbob.core.report import StepReport

err_console = Console(stderr=True)


@dataclass
class StepContext:
    settings: Settings
    course: Course
    force: bool = False
    dry_run: bool = False
    only: set[str] | None = None            # limita a questi stem
    options: dict[str, Any] = field(default_factory=dict)  # opzioni specifiche del passo
    quiet: bool = False
    step: str = ""                          # nome del passo in esecuzione (per il log)
    last_report: StepReport | None = None   # l'ultimo report creato: se il passo si ferma (NeedsHuman) non si perde ciò che era già fatto

    @property
    def layout(self) -> Layout:
        return Layout.of(self.course)

    def manifest(self) -> Manifest:
        return Manifest(self.layout.manifest)

    def log(self, msg: str, level: str = "INFO") -> None:
        """Messaggio per l'utente (stderr) e riga con data e ora in <corso>/.sbob/logs/sbob.log, per il post-mortem.
        Nei log non finiscono mai credenziali: nessun chiamante le passa a log()."""
        if not self.quiet:
            err_console.print(msg, highlight=False)
        self._to_file(level, msg)

    def log_exception(self) -> None:
        """Traceback completo solo su file (all'utente arriva il messaggio breve nel report)."""
        import traceback
        self._to_file("ERROR", traceback.format_exc().rstrip())

    def _to_file(self, level: str, msg: str) -> None:
        import time
        try:
            lay = self.layout
            if self.dry_run and not lay.logs.exists():
                return                                                  # simulazione: non crea cartelle (né il corso) solo per il log
            lay.logs.mkdir(parents=True, exist_ok=True)
            path = lay.logs / "sbob.log"
            if path.exists() and path.stat().st_size > 2_000_000:          # rotazione semplice: un solo file vecchio
                path.replace(lay.logs / "sbob.log.1")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                for line in str(msg).splitlines() or [""]:
                    f.write(f"{stamp} {level:<5} [{self.step or '-'}] {line}\n")
        except OSError:
            pass                                                        # il log non deve mai rompere un passo

    def report(self, step: str) -> StepReport:
        self.last_report = StepReport(step=step, corso=self.course.slug, dry_run=self.dry_run)
        return self.last_report


StepFn = Callable[[StepContext], StepReport]

# Ordine canonico della catena `sbob run`
PIPELINE = ("materiale", "download", "audio", "trascrivi", "appunti", "mappa", "notebook")


def get_step(name: str) -> StepFn:
    """Import lazy: un passo con dipendenze mancanti non rompe gli altri."""
    import importlib

    modules = {
        "download": "sbob.steps.download",
        "audio": "sbob.steps.audio",
        "trascrivi": "sbob.steps.transcribe",
        "appunti": "sbob.steps.notes",
        "merge": "sbob.steps.merge",
        "materiale": "sbob.steps.materiale",
        "mappa": "sbob.steps.mappa",
        "pdf": "sbob.steps.pdf",
        "notebook": "sbob.steps.notebook",
    }
    if name not in modules:
        raise KeyError(f"Passo sconosciuto: {name}")
    return importlib.import_module(modules[name]).run
