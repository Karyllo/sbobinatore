"""Stato persistente per corso in <corso>/.sbob/manifest.json.

Lo stato "fatto/non fatto" di un passo si deduce dai file (fonte di verità = filesystem).
Il manifest conserva solo ciò che dai file non si ricava:
  - videos:    video_id Webex → stem (evita di riscaricare / rinumerare)
  - materiale: path relativo → sha256 dell'ultimo file convertito
  - notebook:  id del taccuino e sorgenti caricate (hash + id), per aggiornare solo ciò che cambia
  - meta:      stem → metadati liberi (backend usato, modello, argomenti, ...)
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from sbob.core.batch import atomic_write_text

_lock = Lock()


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "videos": {}, "materiale": {}, "materiale_conv": {}, "meta": {}, "notebook": {}}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            for k in self.data:
                self.data[k] = loaded.get(k, self.data[k])

    @property
    def videos(self) -> dict[str, str]:
        return self.data["videos"]

    @property
    def materiale(self) -> dict[str, str]:
        return self.data["materiale"]

    @property
    def materiale_conv(self) -> dict[str, dict]:
        """relpath del file in materiale/ → {"sha": sha256 della sorgente, "conv": visione|testo|misto|copia}"""
        return self.data["materiale_conv"]

    @property
    def notebook(self) -> dict[str, Any]:
        """Taccuino NotebookLM del corso: {"id", "persona": hash, "archivi": [anni], "sources": {titolo: {"hash","id"}}}"""
        return self.data["notebook"]

    def meta(self, stem: str) -> dict[str, Any]:
        return self.data["meta"].setdefault(stem, {})

    def save(self) -> None:
        with _lock:
            atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True))
