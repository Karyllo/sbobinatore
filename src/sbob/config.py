"""Caricamento configurazione: sbob.toml + .env.

Risoluzione del file di config (il primo che esiste):
  1. $SBOB_CONFIG
  2. ./sbob.toml
  3. ~/.config/sbob/sbob.toml

Il file .env viene cercato accanto a sbob.toml (poi nella cwd). Le variabili già
presenti nell'ambiente non vengono sovrascritte.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

CONFIG_HOME = Path.home() / ".config" / "sbob"
# Downloader delle registrazioni: fork con le correzioni (pacchetto installabile, archivio recman, click < 8.2).
# Con un indirizzo git uv lo installa da solo in un ambiente isolato: nessun clone manuale.
# TODO quando il fork è pubblicato su GitHub: verificare che questo indirizzo esista.
DEFAULT_DOWNLOADER = "git+https://github.com/Karyllo/polimi_recordings_downloader@local-fixes"
DEFAULT_ROOT = "~/sbob"

# Tipi di sorgente registrazioni supportati dal passo download
SOURCE_TYPES = {"txt", "webeep", "webpage-url", "webpage-html", "archives", "archivio"}
TRANSCRIBE_BACKENDS = {"gemini", "notebooklm", "whisper"}  # whisper: non mantenuto


class ConfigError(Exception):
    pass


@dataclass
class Course:
    """Un corso: identità, dove stanno i suoi file e come vanno trattati."""

    slug: str                       # chiave in [corsi.<slug>], usata nei nomi file
    nome: str
    anno_accademico: str            # "2025-26"
    cartella: Path                  # assoluta, già risolta contro root
    lingua: str = "it"
    sorgente: dict[str, Any] = field(default_factory=dict)       # la prima fonte (compatibilità)
    sorgenti: list[dict[str, Any]] = field(default_factory=list) # tutte le fonti delle registrazioni
    trascrizione: str = "gemini"
    notebook: str | None = None     # nome taccuino NotebookLM (backend notebooklm)
    materiale: Path | None = None   # cartella webeep-sync, se diversa da <cartella>/materiale
    inizio_corso: str | None = None # "YYYY-MM-DD", per calcolare la settimana nel merge
    webeep_id: int | None = None    # id del corso su WeBeep (sbob webeep collega): abilita il passo `materiale`
    archivio: dict[str, int] = field(default_factory=dict)  # edizioni passate: anno → id WeBeep (stesso docente)
    archivio_docente: str | None = None   # docente scelto con `sbob archivio <corso> docenti` (altrimenti quello del corso)
    archivio_di: str | None = None  # valorizzato solo sul Course derivato di un'edizione passata: slug del corso principale
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    path: Path | None               # file sbob.toml caricato (None = default)
    root: Path
    lingua: str
    downloader: str                 # cartella del clone (con o senza .venv) oppure "git+https://..."
    audio_bitrate: str
    modelli: dict[str, dict[str, Any]]   # ruolo → {provider, model, ...}
    providers: dict[str, dict[str, Any]] # nome → {tipo, chiavi, base_url, ...}
    corsi: dict[str, Course]
    raw: dict[str, Any]

    def corso(self, slug: str) -> Course:
        try:
            return self.corsi[slug]
        except KeyError:
            disponibili = ", ".join(sorted(self.corsi)) or "(nessuno)"
            raise ConfigError(f"Corso '{slug}' non trovato. Disponibili: {disponibili}") from None


# Default sensati: si possono sovrascrivere tutti in sbob.toml
DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {"tipo": "gemini", "chiavi": "GOOGLE_API_KEY"},
    "deepseek": {"tipo": "openai", "chiavi": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
                 "thinking_param": "deepseek"},  # V4 ragiona per default: va spento esplicitamente
    "openai": {"tipo": "openai", "chiavi": "OPENAI_API_KEY"},
    "anthropic": {"tipo": "anthropic", "chiavi": "ANTHROPIC_API_KEY"},
}

DEFAULT_MODELLI: dict[str, dict[str, Any]] = {
    "trascrizione": {"provider": "gemini", "model": "gemini-3-flash-preview", "rpm": 10, "max_tokens": 65536,
                     "temperature": 0.0},
    "refiner": {"provider": "gemini", "model": "gemini-3.1-flash-lite", "rpm": 30,
                "temperature": 0.1, "max_tokens": 8192, "tentativi": 3},
    "notes": {"provider": "gemini", "model": "gemini-3.5-flash", "rpm": 10,
              "temperature": 0.3, "top_p": 0.95, "max_tokens": 65536, "thinking": True},
    "pdf": {"provider": "gemini", "model": "gemini-3.8-flash", "rpm": 10,
            # stessa chiave, altro modello = quota separata: prima riserva quando finisce quella principale
            "riserva": {"provider": "gemini", "model": "gemini-3-flash-preview", "rpm": 10}},
    # solo testo (niente visione): usato dal passo materiale quando anche la riserva è senza quota
    "pdf_testo": {"provider": "deepseek", "model": "deepseek-v4-flash", "rpm": 200, "temperature": 0.1,
                  "max_tokens": 8192, "thinking": False, "workers": 6},
    "mappa": {"provider": "gemini", "model": "gemini-3.1-flash-lite", "rpm": 30, "temperature": 0.2,
              "max_tokens": 4096},
}


def find_config_file() -> Path | None:
    candidates = []
    if env := os.environ.get("SBOB_CONFIG"):
        candidates.append(Path(env).expanduser())
    candidates += [Path.cwd() / "sbob.toml", CONFIG_HOME / "sbob.toml"]
    return next((p for p in candidates if p.is_file()), None)


def _expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def _merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for k, v in over.items():
        out[k] = {**out.get(k, {}), **v}
    return out


def _parse_course(slug: str, data: dict[str, Any], root: Path, lingua: str) -> Course:
    for key in ("nome", "anno_accademico"):
        if key not in data:
            raise ConfigError(f"[corsi.{slug}]: manca '{key}'")
    cartella = data.get("cartella", f"{data['anno_accademico']}/{slug}")
    cartella_path = _expand(cartella)
    if not cartella_path.is_absolute():
        cartella_path = root / cartella_path

    # Le registrazioni possono stare in posti diversi (WeBeep, archivio recman, sito del docente, link diretti):
    # `sorgenti = [ {...}, {...} ]` per più fonti, oppure `sorgente = {...}` per una sola.
    raw_sources = data.get("sorgenti") or ([data["sorgente"]] if data.get("sorgente") else [])
    sorgenti = [dict(s) for s in raw_sources]
    for s in sorgenti:
        if s.get("tipo") not in SOURCE_TYPES:
            raise ConfigError(f"[corsi.{slug}] sorgente tipo deve essere uno di {sorted(SOURCE_TYPES)} (trovato: {s.get('tipo')!r})")
    sorgente = sorgenti[0] if sorgenti else {}

    trascrizione = data.get("trascrizione", "gemini")
    if trascrizione not in TRANSCRIBE_BACKENDS:
        raise ConfigError(f"[corsi.{slug}].trascrizione deve essere uno di {sorted(TRANSCRIBE_BACKENDS)}")

    known = {"nome", "anno_accademico", "cartella", "lingua", "sorgente", "sorgenti", "archivio", "archivio_docente", "trascrizione",
             "notebook", "materiale", "inizio_corso", "webeep_id"}
    return Course(
        slug=slug,
        nome=data["nome"],
        anno_accademico=data["anno_accademico"],
        cartella=cartella_path,
        lingua=data.get("lingua", lingua),
        sorgente=sorgente,
        sorgenti=sorgenti,
        trascrizione=trascrizione,
        notebook=data.get("notebook"),
        materiale=_expand(data["materiale"]) if data.get("materiale") else None,
        inizio_corso=data.get("inizio_corso"),
        webeep_id=int(data["webeep_id"]) if data.get("webeep_id") else None,
        archivio={str(k): int(v) for k, v in (data.get("archivio") or {}).items()},
        archivio_docente=data.get("archivio_docente"),
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_settings(path: Path | None = None) -> Settings:
    path = path or find_config_file()
    raw: dict[str, Any] = {}
    if path:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        load_dotenv(path.parent / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)

    root = _expand(raw.get("root", DEFAULT_ROOT))
    lingua = raw.get("lingua", "it")
    corsi = {slug: _parse_course(slug, data, root, lingua)
             for slug, data in raw.get("corsi", {}).items()}

    return Settings(
        path=path,
        root=root,
        lingua=lingua,
        downloader=raw.get("downloader", DEFAULT_DOWNLOADER),
        audio_bitrate=raw.get("audio", {}).get("bitrate", "32k"),
        modelli=_merge(DEFAULT_MODELLI, raw.get("modelli", {})),
        providers=_merge(DEFAULT_PROVIDERS, raw.get("providers", {})),
        corsi=corsi,
        raw=raw,
    )
