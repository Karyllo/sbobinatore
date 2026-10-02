"""Frontmatter YAML per trascrizioni e appunti: è ciò che rende i file navigabili da un agente.

Campi standard (tutti opzionali tranne corso):
  corso, anno, data, lezione (stem), tipo, numero, sorgente, backend, modello, argomenti: [..]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_FENCE = "---"


def split(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith(_FENCE + "\n"):
        end = text.find("\n" + _FENCE, len(_FENCE))
        if end != -1:
            meta = yaml.safe_load(text[len(_FENCE) + 1 : end]) or {}
            body = text[end + len(_FENCE) + 1 :].lstrip("\n")
            return (meta if isinstance(meta, dict) else {}), body
    return {}, text


def join(meta: dict[str, Any], body: str) -> str:
    clean = {k: v for k, v in meta.items() if v is not None}
    if not clean:
        return body
    head = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
    return f"{_FENCE}\n{head}\n{_FENCE}\n\n{body.lstrip()}"


def read(path: Path) -> tuple[dict[str, Any], str]:
    return split(path.read_text(encoding="utf-8"))


def lesson_meta(course, stem: str, **extra: Any) -> dict[str, Any]:
    """Metadati base derivati dal nome della lezione."""
    from sbob.core import naming

    n = naming.parse(stem)
    meta: dict[str, Any] = {"corso": course.nome, "slug": course.slug, "anno": course.anno_accademico,
                            "lezione": stem}
    if n:
        meta.update(data=n.data.isoformat(), tipo=n.tipo, numero=n.num)
    meta.update(extra)
    return meta
