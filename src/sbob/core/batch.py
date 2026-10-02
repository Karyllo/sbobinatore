"""Pattern comune a tutti i passi: "per ogni file in ingresso, produci un output, salta se c'è già"."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Job:
    src: Path
    dst: Path


def list_inputs(folder: Path, extensions: Iterable[str]) -> list[Path]:
    """File (non nascosti) con estensione tra quelle date, case-insensitive, ordinati per nome."""
    if not folder.is_dir():
        return []
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts)


def list_tree(folder: Path, extensions: Iterable[str]) -> list[Path]:
    """Come list_inputs ma scende nelle sottocartelle (esclusi file e cartelle nascosti)."""
    if not folder.is_dir():
        return []
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in exts
                  and not any(part.startswith(".") for part in p.relative_to(folder).parts))


def plan_jobs(inputs: Iterable[Path], dst_for: Callable[[Path], Path], force: bool = False,
              only: set[str] | None = None) -> tuple[list[Job], list[Job]]:
    """Divide in (da_fare, già_fatti). `only` limita agli stem indicati."""
    todo: list[Job] = []
    done: list[Job] = []
    for src in inputs:
        if only and src.stem not in only:
            continue
        job = Job(src, dst_for(src))
        (todo if force or not job.dst.exists() else done).append(job)
    return todo, done


def atomic_write_text(path: Path, text: str) -> None:
    """Scrive su file temporaneo e rinomina: un crash non lascia mai output a metà
    (che verrebbe poi saltato come 'già fatto')."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
