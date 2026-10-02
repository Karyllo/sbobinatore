"""Contratto di uscita di ogni passo: lo stesso oggetto serve al terminale (rich) e agli agenti (--json).

Exit code:
  0 OK        tutto fatto o già fatto
  1 ERROR     errore bloccante (config, dipendenze, eccezione)
  2 PARTIAL   alcuni file falliti, altri ok
  3 HUMAN     serve un'azione umana (cookie scaduto, login NotebookLM, chiave mancante)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any


class Exit(IntEnum):
    OK = 0
    ERROR = 1
    PARTIAL = 2
    HUMAN = 3


class NeedsHuman(Exception):
    """Da sollevare quando serve un intervento dell'utente: il passo si ferma con exit 3."""

    def __init__(self, message: str, action: str | None = None):
        super().__init__(message)
        self.action = action  # comando o istruzione suggerita, es. "sbob cookie ticket <valore>"


@dataclass
class Failure:
    item: str
    error: str


@dataclass
class StepReport:
    step: str
    corso: str | None = None
    done: list[str] = field(default_factory=list)      # item elaborati in questo run
    skipped: list[str] = field(default_factory=list)   # già fatti
    failed: list[Failure] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)   # path prodotti
    cost: dict[str, Any] = field(default_factory=dict) # riepilogo costi (llm.cost)
    needs_human: str | None = None
    action: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)     # messaggi informativi
    warnings: list[str] = field(default_factory=list)  # qualcosa è andato a metà (es. quota esaurita): exit 2
    dry_run: bool = False

    def fail(self, item: str, error: Exception | str) -> None:
        self.failed.append(Failure(item, str(error)))

    @property
    def exit_code(self) -> Exit:
        if self.needs_human:
            return Exit.HUMAN
        if self.error:
            return Exit.ERROR
        if self.warnings and not self.failed:
            return Exit.PARTIAL
        if self.failed:
            return Exit.PARTIAL if self.done or self.skipped else Exit.ERROR
        return Exit.OK

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["exit_code"] = int(self.exit_code)
        d["ok"] = self.exit_code == Exit.OK
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
