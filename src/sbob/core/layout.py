"""Struttura delle cartelle di un corso.

<cartella corso>/
  materiale/      file del corso (webeep-sync o a mano)
  video/          registrazioni .mp4
  audio/          .aac estratti dai video
  trascrizioni/   .md (testo della lezione, con frontmatter)
  appunti/        .md (dispense generate, con frontmatter)
  merge/          file uniti per NotebookLM/LLM
  .sbob/          stato interno: manifest, costi, staging, log, md del materiale
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sbob.config import Course

STEP_DIRS = ("materiale", "materiale_md", "video", "audio", "trascrizioni", "appunti", "merge")


@dataclass(frozen=True)
class Layout:
    base: Path
    materiale_override: Path | None = None

    @classmethod
    def of(cls, course: Course) -> "Layout":
        return cls(course.cartella, course.materiale)

    @property
    def materiale(self) -> Path:
        return self.materiale_override or self.base / "materiale"

    @property
    def materiale_md(self) -> Path:
        """Materiale convertito in Markdown (stessa struttura di materiale/): quello che legge l'agente."""
        return self.base / "materiale_md"

    @property
    def video(self) -> Path:
        return self.base / "video"

    @property
    def audio(self) -> Path:
        return self.base / "audio"

    @property
    def trascrizioni(self) -> Path:
        return self.base / "trascrizioni"

    @property
    def appunti(self) -> Path:
        return self.base / "appunti"

    @property
    def merge(self) -> Path:
        return self.base / "merge"

    @property
    def mappa(self) -> Path:
        """Livello di navigazione per l'agente (riassunti, concetti, indice): separato dagli appunti."""
        return self.base / "mappa"

    @property
    def state(self) -> Path:
        return self.base / ".sbob"

    @property
    def staging(self) -> Path:
        return self.state / "staging"

    @property
    def manifest(self) -> Path:
        return self.state / "manifest.json"

    @property
    def costs(self) -> Path:
        return self.state / "costs.jsonl"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    def ensure(self, *names: str) -> None:
        """Crea le cartelle indicate (o tutte se nessun nome). Creazione lazy: ogni passo crea solo ciò che usa."""
        for name in names or (*STEP_DIRS, "state"):
            getattr(self, name).mkdir(parents=True, exist_ok=True)
