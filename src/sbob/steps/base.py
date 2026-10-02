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

    @property
    def layout(self) -> Layout:
        return Layout.of(self.course)

    def manifest(self) -> Manifest:
        return Manifest(self.layout.manifest)

    def log(self, msg: str) -> None:
        if not self.quiet:
            err_console.print(msg, highlight=False)

    def report(self, step: str) -> StepReport:
        return StepReport(step=step, corso=self.course.slug, dry_run=self.dry_run)


StepFn = Callable[[StepContext], StepReport]

# Ordine canonico della catena `sbob run`
PIPELINE = ("download", "audio", "trascrivi", "appunti", "mappa")


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
    }
    if name not in modules:
        raise KeyError(f"Passo sconosciuto: {name}")
    return importlib.import_module(modules[name]).run
