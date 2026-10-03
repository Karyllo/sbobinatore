"""Quali modelli esistono, quali usano i ruoli e cosa è cambiato: sbob se ne accorge da solo e lo segnala.

L'elenco dei modelli si chiede al provider (per Gemini `models.list`: gratis, non consuma quota). Non si cambia mai
niente in automatico: un modello più nuovo non è per forza migliore per quel ruolo (l'output può cambiare, la quota
giornaliera è diversa, i nuovi sono spesso sovraccarichi). Si avvisa, e si suggerisce come provarlo su una lezione.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sbob.config import CONFIG_HOME, Settings

SEEN_FILE = CONFIG_HOME / "modelli_visti.json"
_TEXT = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(flash-lite|flash|pro)(-preview)?$")
_SKIP = ("tts", "image", "robotics", "computer-use", "customtools", "omni", "embedding", "live")
NOTE = ("Un modello più nuovo non è per forza migliore per quel ruolo: l'output può cambiare, la quota giornaliera "
        "(sul gratuito 20 richieste per modello) è diversa e i modelli appena usciti sono spesso sovraccarichi. "
        "sbob non cambia niente da solo: provalo su una lezione e confronta.")


def family(name: str) -> tuple[tuple[int, ...], str, bool] | None:
    """'gemini-3.6-flash' → ((3, 6), 'flash', False); None se non è un modello di testo/ragionamento standard."""
    name = name.removeprefix("models/")
    if any(s in name for s in _SKIP):
        return None
    m = _TEXT.match(name)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split(".")), m.group(2), bool(m.group(3))


def available_gemini(client: Any) -> list[str]:
    """Nomi dei modelli Gemini che generano contenuto (`models.list`, senza consumare quota)."""
    out = []
    for m in client.models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []) and "gemini" in m.name:
            out.append(m.name.removeprefix("models/"))
    return sorted(out)


def _label(name: str) -> str:
    f = family(name)
    return name if not f else f"{'.'.join(map(str, f[0]))} {f[1].replace('-', ' ').title()}"


def role_models(settings: Settings) -> list[dict[str, str]]:
    """[{ruolo, modello, tipo}] dei modelli Gemini configurati (principali e riserve)."""
    rows = []
    for role, conf in settings.modelli.items():
        if conf.get("provider") == "gemini" and conf.get("model"):
            rows.append({"ruolo": role, "modello": conf["model"], "tipo": "principale"})
        riserva = conf.get("riserva")
        if isinstance(riserva, dict) and riserva.get("provider") == "gemini" and riserva.get("model"):
            rows.append({"ruolo": role, "modello": riserva["model"], "tipo": "riserva"})
    return rows


def analyze(configured: list[dict[str, str]], available: list[str], previous: list[str] | None) -> dict[str, Any]:
    """Confronta ciò che i ruoli usano con ciò che esiste. Funzione pura (testabile senza rete)."""
    have = set(available)
    stable = [n for n in available if (f := family(n)) and not f[2]]
    roles, warnings = [], []
    for row in configured:
        name, fam = row["modello"], family(row["modello"])
        entry: dict[str, Any] = {**row, "disponibile": name in have, "piu_nuovi": []}
        if name not in have:
            warnings.append(f"{row['ruolo']} ({row['tipo']}): il modello {name} non è più nell'elenco di Google: "
                            "le chiamate falliranno. Scegline un altro in [modelli.%s] di sbob.toml." % row["ruolo"])
        if fam:
            newer = sorted((n for n in stable if (f := family(n)) and f[1] == fam[1] and f[0] > fam[0]),
                           key=lambda n: family(n)[0])  # type: ignore[index]
            entry["piu_nuovi"] = newer
            if fam[2] and name in have:
                better = [n for n in stable if (f := family(n)) and f[1] == fam[1] and f[0] >= fam[0]]
                warnings.append(f"{row['ruolo']} ({row['tipo']}): {name} è una preview e può essere ritirata"
                                + (f"; esiste lo stabile {better[0]}." if better else "."))
        roles.append(entry)
    # novità per famiglia: confronto con il più nuovo che sbob usa in QUALSIASI ruolo (principale o riserva), così le scelte
    # volute (modelli diversi per non saturare una sola quota) non diventano falsi allarmi
    for fam_name in ("flash", "flash-lite", "pro"):
        used = [(family(r["modello"])[0], r["ruolo"]) for r in configured  # type: ignore[index]
                if (f := family(r["modello"])) and f[1] == fam_name]
        if not used:
            continue
        top, where = max(used)
        newer = sorted((n for n in stable if (f := family(n)) and f[1] == fam_name and f[0] > top),
                       key=lambda n: family(n)[0])  # type: ignore[index]
        if newer:
            label = fam_name.replace("-", " ").title()
            warnings.append(f"{label}: il più nuovo che usi è {'.'.join(map(str, top))} (ruolo {where}); "
                            f"sono disponibili {', '.join(_label(n) for n in newer)}.")
    new_since = sorted(set(available) - set(previous)) if previous else []
    notable = [n for n in new_since if (family(n) or ("transcribe" in n,))]
    special = sorted(n for n in available if "transcribe" in n)
    if special and not any("transcribe" in r["modello"] for r in configured):
        warnings.append(f"Esiste un modello dedicato alla trascrizione ({', '.join(special)}). Provato da sbob il 2026-10-03 su "
                        "3 minuti di lezione: è velocissimo e fedele, ma NON mette i timestamp (li ignora anche se richiesti) e sul "
                        "gratuito ha un limite di 10.000 token di ingresso al minuto (circa 6 minuti di audio al minuto). "
                        "Utile solo se i timestamp non servono; con i timestamp restano i modelli Flash/Flash-Lite.")
    if notable:
        warnings.append("Nuovi dall'ultimo controllo: " + ", ".join(notable) + ".")
    aliases = sorted(n for n in available if n.endswith("-latest"))
    return {"ruoli": roles, "nuovi_dall_ultimo_controllo": new_since, "speciali": special, "alias_latest": aliases,
            "avvisi": warnings, "nota": NOTE if any("sono disponibili" in w or "Nuovi" in w for w in warnings) else None}


def load_seen() -> list[str] | None:
    try:
        return json.loads(SEEN_FILE.read_text()).get("modelli")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def save_seen(available: list[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"modelli": available, "quando": time.strftime("%Y-%m-%d %H:%M")}))


def check(settings: Settings, remember: bool = True) -> dict[str, Any]:
    """Interroga Google (gratis) e restituisce `analyze(...)`. Senza chiave Gemini restituisce solo una nota."""
    from sbob.core.keys import MissingKeyError, load_keys

    conf = settings.providers.get("gemini", {})
    try:
        key = load_keys(conf.get("chiavi", "GOOGLE_API_KEY"))[0]
    except (MissingKeyError, IndexError):
        return {"ruoli": [], "avvisi": ["Nessuna chiave Gemini: non posso controllare i modelli."], "nota": None,
                "nuovi_dall_ultimo_controllo": [], "speciali": [], "alias_latest": []}
    from google import genai

    available = available_gemini(genai.Client(api_key=key))
    result = analyze(role_models(settings), available, load_seen())
    if remember:
        save_seen(available)
    return result
