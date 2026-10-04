"""`sbob init` e `sbob aggiungi-corso`: creano la config facendo domande, così nessuno deve modificare file a mano.

Le funzioni `render_*` sono pure (testabili); le parti interattive usano questionary.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from sbob.config import CONFIG_HOME, SOURCE_TYPES
from sbob.core.naming import slugify

# Preset per gli appunti: (provider, modello, parametri, prefisso chiavi, riserva)
PRESETS: dict[str, dict[str, Any]] = {
    # Gemini usa i default di sbob (DEFAULT_MODELLI): ruoli distribuiti su modelli diversi con riserve, tutti Gemini.
    # Quindi basta UNA chiave (niente DeepSeek) e nel file di configurazione non si scrive nessun modello.
    "gemini": {"label": "Gemini (gratuito con limiti: pochi blocchi al giorno per i modelli Flash (circa 20 richieste), molti di più per i Lite; i modelli si alternano da soli)",
               "notes": None},
    "deepseek": {"label": "DeepSeek (a pagamento, economico, senza limiti giornalieri)",
                 "notes": {"provider": "deepseek", "model": "deepseek-v4-pro", "rpm": 200, "workers": 8,
                           "temperature": 0.3, "max_tokens": 65536, "thinking": False}},
    "anthropic": {"label": "Claude (a pagamento, qualità alta)",
                  "notes": {"provider": "anthropic", "model": "claude-sonnet-5-5", "rpm": 50, "workers": 4,
                            "max_tokens": 32000, "thinking": True}},
}
KEY_PREFIX = {"gemini": "GOOGLE_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def q(value: Any) -> str:
    """Valore TOML: le stringhe JSON sono stringhe TOML valide (stessi escape)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(str(k))} = {q(v)}" for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def render_course(slug: str, nome: str, anno: str, cartella: str, sorgente: dict | None, lingua: str = "it") -> str:
    lines = [f"[corsi.{slug}]", f"nome = {q(nome)}", f"anno_accademico = {q(anno)}", f"cartella = {q(cartella)}"]
    if lingua != "it":
        lines.append(f"lingua = {q(lingua)}")
    if sorgente:
        lines.append(f"sorgente = {q(sorgente)}")
    lines.append('trascrizione = "gemini"')
    return "\n".join(lines) + "\n"


def render_config(root: str, lingua: str, preset: str, courses: Sequence[str] = ()) -> str:
    p = PRESETS[preset]
    parts = [
        "# Creato da `sbob init`. Documentazione delle opzioni: sbob.example.toml nel repo.",
        f"root = {q(root)}",
        f"lingua = {q(lingua)}",
        "",
    ]
    if p["notes"]:                          # Gemini: nessun blocco, valgono i default (vedi `sbob modelli`)
        parts += ["[modelli.notes]", *(f"{k} = {q(v)}" for k, v in p["notes"].items())]
        if p.get("riserva"):
            parts.append(f"riserva = {q(p['riserva'])}")
        parts += ["", "[modelli.refiner]", *(f"{k} = {q(v)}" for k, v in {
            **p["notes"], "temperature": 0.1, "max_tokens": 8192, "thinking": False, "tentativi": 3}.items())]
    parts.append("")
    return "\n".join(parts) + "".join("\n" + c for c in courses)


def set_course_field(cfg: Path, slug: str, key: str, value: Any) -> None:
    """Scrive/aggiorna `key = value` nel blocco [corsi.<slug>] di sbob.toml, senza toccare il resto del file."""
    if cfg is None or not cfg.exists():
        raise ValueError("sbob.toml non trovato")
    lines = cfg.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[corsi.{slug}]"), None)
    if start is None:
        raise ValueError(f"[corsi.{slug}] non trovato in {cfg}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    new = f"{key} = {q(value)}"
    for i in range(start + 1, end):
        if re.match(rf"\s*{re.escape(key)}\s*=", lines[i]):
            lines[i] = new
            break
    else:
        pos = end
        while pos > start + 1 and not lines[pos - 1].strip():      # dopo l'ultima riga non vuota del blocco
            pos -= 1
        lines.insert(pos, new)
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_course_field(cfg: Path, slug: str, key: str) -> bool:
    """Toglie `key` dal blocco [corsi.<slug>] di sbob.toml. True se c'era."""
    lines = cfg.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[corsi.{slug}]"), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"\s*{re.escape(key)}\s*=", lines[i]):
            del lines[i]
            cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def needed_providers(preset: str) -> list[str]:
    """Provider di cui servono le chiavi: Gemini sempre (trascrizione e PDF), più quelli degli appunti."""
    p = PRESETS[preset]
    names = ["gemini"] + ([p["notes"]["provider"]] if p["notes"] else []) + ([p["riserva"]["provider"]] if p.get("riserva") else [])
    return list(dict.fromkeys(names))


def write_env(path: Path, keys: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [l for l in existing.splitlines() if l.split("=", 1)[0].strip() not in keys]
    lines += [f"{k}={v}" for k, v in keys.items() if v]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)                   # contiene segreti: leggibile solo dall'utente


# ------------------------------------------------------------------ interattivo

def _ask_course(questionary, root: Path) -> str | None:
    nome = questionary.text("Nome del corso (es. Elettronica; Invio senza scrivere = ho finito):").ask()
    if not nome:
        return None
    slug = questionary.text("Nome breve, usato nei file:", default=slugify(nome)).ask() or slugify(nome)
    anno = questionary.text("Anno accademico:", default="2025-26").ask()
    cartella = questionary.text(f"Cartella del corso (relativa a {root}):", default=slug).ask()
    tipo = questionary.select("Da dove arrivano le registrazioni?", choices=[
        questionary.Choice("File di link (link.txt nella cartella del corso)", value="txt"),
        questionary.Choice("Pagina del corso su WeBeep", value="webeep"),
        questionary.Choice("Per ora nessuna", value=None)]).ask()
    sorgente = None
    if tipo == "txt":
        sorgente = {"tipo": "txt", "file": "link.txt"}
    elif tipo == "webeep":
        url = questionary.text("Indirizzo della pagina WeBeep del corso:").ask()
        sorgente = {"tipo": "webeep", "url": url} if url else None
    return render_course(slug, nome, anno, cartella, sorgente)


def init(force: bool = False) -> int:
    import questionary

    cfg = CONFIG_HOME / "sbob.toml"
    if cfg.exists() and not force:
        print(f"Esiste già {cfg}. Usa `sbob aggiungi-corso`, oppure `sbob init --force` per ricrearla.")
        return 1
    root = questionary.path("Cartella che contiene i tuoi corsi:", default=str(Path.home() / "Università"),
                            only_directories=True).ask()
    if not root:
        return 1
    lingua = questionary.select("Lingua delle lezioni:", choices=["it", "en"]).ask()
    preset = questionary.select("Con quale modello vuoi generare gli appunti?",
                                choices=[questionary.Choice(v["label"], value=k) for k, v in PRESETS.items()]).ask()
    keys: dict[str, str] = {}
    print("Chiavi API (restano solo sul tuo computer, nel file .env).")
    print("La chiave Gemini si prende gratis da https://aistudio.google.com/apikey (due minuti).")
    for prov in needed_providers(preset):
        while True:
            val = questionary.password(f"Chiave {prov} ({KEY_PREFIX[prov]}):").ask()
            if val and val.strip():
                keys[f"{KEY_PREFIX[prov]}_ACCOUNT1"] = val.strip()
                break
            # senza chiave sbob non può trascrivere né fare appunti: meglio dirlo ora che a metà del lavoro
            if questionary.confirm(f"Senza la chiave {prov} sbob non funziona. Continuo lo stesso e la metto dopo "
                                   f"(in {CONFIG_HOME / '.env'})?", default=False).ask():
                break
    courses: list[str] = []
    if questionary.confirm("Vuoi aggiungere un corso a mano adesso? (Più comodo dopo, con `sbob webeep scegli`: "
                           "li scegli da un elenco)", default=False).ask():
        while c := _ask_course(questionary, Path(root).expanduser()):      # nome vuoto = finito
            courses.append(c)
            if not questionary.confirm("Un altro corso?", default=False).ask():
                break
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    cfg.write_text(render_config(root, lingua, preset, courses), encoding="utf-8")
    write_env(CONFIG_HOME / ".env", keys)
    print(f"Creati {cfg} e {CONFIG_HOME / '.env'}.")
    print("Prossimi passi:  sbob doctor  →  sbob login  →  sbob webeep scegli")
    return 0


def add_course() -> int:
    import questionary

    from sbob.config import find_config_file, load_settings

    cfg = find_config_file()
    if not cfg:
        print("Nessuna configurazione: lancia prima `sbob init`.")
        return 1
    block = _ask_course(questionary, load_settings(cfg).root)
    if not block:
        return 1
    with open(cfg, "a", encoding="utf-8") as f:
        f.write("\n" + block)
    print(f"Corso aggiunto a {cfg}.")
    return 0


assert set(KEY_PREFIX) == set(PRESETS) and SOURCE_TYPES  # coerenza dei preset
