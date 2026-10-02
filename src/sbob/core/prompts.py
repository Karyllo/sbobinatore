"""Prompt in src/sbob/prompts/<lingua>/<nome>.md (placeholder str.format). Fuori dal codice: si modificano senza toccarlo."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load(lingua: str, nome: str, variante: str | None = None) -> str:
    """`variante` (es. il provider): se esiste <nome>.<variante>.md si usa quella, altrimenti <nome>.md.
    Serve a tarare il prompt su modelli che interpretano le stesse istruzioni in modo diverso."""
    if variante and (v := _DIR / lingua / f"{nome}.{variante}.md").exists():
        return v.read_text(encoding="utf-8")
    path = _DIR / lingua / f"{nome}.md"
    if not path.exists():
        langs = sorted(p.name for p in _DIR.iterdir() if p.is_dir())
        raise FileNotFoundError(f"Prompt '{nome}' non disponibile per la lingua '{lingua}' (lingue: {langs})")
    return path.read_text(encoding="utf-8")


def render(lingua: str, nome: str, variante: str | None = None, **values: str) -> str:
    return load(lingua, nome, variante).format(**values)
