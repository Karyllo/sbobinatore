"""Contratto provider-agnostico per le chiamate LLM.

Un adapter (llm/gemini.py, llm/openai_compat.py, llm/anthropic.py) implementa `Provider.complete`
e NON solleva eccezioni per errori dell'API: restituisce LLMResult con error_kind valorizzato.
La logica di retry vive in un solo punto (llm.registry.Role.complete), non negli adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"   # 429 temporaneo → attendi e ritenta
    QUOTA = "quota"             # quota giornaliera / credito finito → cambia chiave, non insistere
    AUTH = "auth"               # chiave non valida → NeedsHuman
    SERVER = "server"           # 5xx, timeout, rete → ritenta con backoff
    BLOCKED = "blocked"         # safety / contenuto rifiutato → non ritentare uguale
    BAD_REQUEST = "bad_request" # 4xx di input (modello inesistente, troppo lungo) → non ritentare
    OTHER = "other"


RETRYABLE = {ErrorKind.RATE_LIMIT, ErrorKind.SERVER, ErrorKind.OTHER}


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str = "image/png"


@dataclass(frozen=True)
class FilePart:
    """File locale da allegare (audio/pdf). Gli adapter che non lo supportano rispondono BAD_REQUEST."""
    path: Path
    mime_type: str


Part = TextPart | ImagePart | FilePart


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant"
    parts: tuple[Part, ...]

    @classmethod
    def user(cls, *parts: Part | str) -> "Message":
        return cls("user", tuple(TextPart(p) if isinstance(p, str) else p for p in parts))


@dataclass
class Params:
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    thinking: bool = False          # ragionamento esteso: ogni adapter lo mappa al proprio meccanismo
    system: str | None = None
    timeout: float = 600.0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0


@dataclass
class LLMResult:
    text: str | None = None
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None   # "stop" | "length" | "safety" | ...
    error_kind: ErrorKind | None = None
    error: str | None = None
    provider: str = ""
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.error_kind is None and bool(self.text)


class Provider(Protocol):
    """Un'istanza per (provider, chiave): crea il client SDK una volta sola."""

    name: str

    def complete(self, messages: list[Message], model: str, params: Params) -> LLMResult: ...
