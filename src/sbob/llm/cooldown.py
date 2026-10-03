"""Modelli senza quota: si ricorda fino a quando, così i comandi successivi non devono riscoprirlo con un errore.

Un 429 "quota giornaliera" arriva subito e non consuma richieste, ma dice anche quanto aspettare ("retry in 15h10m"):
lo si salva in ~/.config/sbob/quota.json per modello e chiave (la chiave solo come hash, mai in chiaro).
Una quota finita non si può interrogare senza spendere una richiesta: l'unico segnale affidabile è l'errore stesso.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from sbob.config import CONFIG_HOME

PATH = CONFIG_HOME / "quota.json"
DEFAULT_WAIT = 30 * 60                      # senza indicazione dal provider (es. credito finito): si riprova tra 30 minuti
MAX_WAIT = 2 * 3600                         # tetto prudente: un 429 non costa nulla, e se la quota torna prima non si blocca un modello disponibile
_RETRY = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


def slot_id(provider: str, model: str, key: str) -> str:
    return hashlib.sha256(f"{provider}|{model}|{key}".encode()).hexdigest()[:16]


def parse_wait(error_text: str | None) -> float:
    """Secondi da aspettare dall'errore del provider (al massimo MAX_WAIT: un "retry in 16h" non va preso alla lettera,
    meglio riscoprire la quota ogni due ore); DEFAULT_WAIT se non c'è l'indicazione."""
    if error_text and (m := _RETRY.search(error_text)) and any(m.groups()):
        h, mi, s = (float(x) if x else 0.0 for x in m.groups())
        return min(MAX_WAIT, max(60.0, h * 3600 + mi * 60 + s))
    return DEFAULT_WAIT


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(f".{PATH.name}.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(PATH)


def until(sid: str) -> float | None:
    """Istante (epoch) fino a cui lo slot è senza quota, se ancora nel futuro."""
    entry = _load().get(sid)
    return float(entry["until"]) if entry and float(entry["until"]) > time.time() else None


def mark(sid: str, label: str, seconds: float) -> None:
    data = {k: v for k, v in _load().items() if float(v.get("until", 0)) > time.time()}
    data[sid] = {"label": label, "until": time.time() + seconds}
    _save(data)


def status() -> list[dict]:
    """[{modello, ancora_s}] di ciò che è ancora in attesa di quota."""
    now = time.time()
    rows = [{"modello": v["label"], "ancora_s": int(float(v["until"]) - now)} for v in _load().values()
            if float(v.get("until", 0)) > now]
    return sorted(rows, key=lambda r: r["ancora_s"])


def clear() -> None:
    PATH.unlink(missing_ok=True)
