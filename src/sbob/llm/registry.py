"""Ruoli → provider/modello, con pool di chiavi, rate limit, retry e costi in un solo posto.

Uso:
    reg = Registry(settings, tracker)
    role = reg.role("notes")             # legge [modelli.notes] da sbob.toml
    result = role.complete([Message.user(prompt)], item="lez03#2")

Config di un ruolo ([modelli.<ruolo>] in sbob.toml):
    provider = "deepseek"        # chiave in [providers.*]
    model = "deepseek-v4-pro"
    rpm = 200                    # richieste/minuto PER CHIAVE
    temperature, top_p, max_tokens, thinking, system   (opzionali → Params)
    prezzi = { input = 0.4, output = 1.6, cached_input = 0.04 }   # USD / Mtok, opzionale
    tentativi = 5
    riserva = "deepseek:deepseek-v4-pro"   # oppure { provider = "deepseek", model = "...", thinking = false, ... }
                                           # usata quando TUTTE le chiavi del principale finiscono la quota

Config di un provider ([providers.<nome>]):
    tipo = "gemini" | "openai" | "anthropic"
    chiavi = "DEEPSEEK_API_KEY"   # prefisso env, vedi core.keys
    base_url = "..."              # solo tipo openai
"""

from __future__ import annotations

import itertools
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from threading import Lock
from typing import Any

from sbob.config import ConfigError, Settings
from sbob.core.keys import MissingKeyError, load_keys
from sbob.llm import cooldown
from sbob.core.ratelimit import LimiterPool
from sbob.core.report import NeedsHuman, QuotaExhausted
from sbob.llm.base import RETRYABLE, ErrorKind, LLMResult, Message, Params, Provider
from sbob.llm.cost import CostTracker, Prices

_PARAM_FIELDS = {f.name for f in fields(Params)}


def _make_provider(tipo: str, name: str, key: str, conf: dict[str, Any]) -> Provider:
    if tipo == "gemini":
        from sbob.llm.gemini import GeminiProvider
        return GeminiProvider(name, key, conf)
    if tipo == "openai":
        from sbob.llm.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(name, key, conf)
    if tipo == "anthropic":
        from sbob.llm.anthropic import AnthropicProvider
        return AnthropicProvider(name, key, conf)
    raise ConfigError(f"Provider '{name}': tipo sconosciuto '{tipo}' (gemini|openai|anthropic)")


@dataclass
class _KeySlot:
    key: str
    client: Provider
    dead: bool = False   # quota esaurita in questo run


class Role:
    def __init__(self, name: str, conf: dict[str, Any], provider_conf: dict[str, Any],
                 limiters: LimiterPool, tracker: CostTracker):
        self.name = name
        self.conf = conf                     # opzioni specifiche del ruolo (es. pdf_modo, pagine_per_blocco)
        self.provider_name = conf["provider"]
        self.model = conf["model"]
        self.rpm = int(conf.get("rpm", 10))
        self.attempts = int(conf.get("tentativi", 5))
        self._workers_conf = conf.get("workers")
        self.params = Params(**{k: v for k, v in conf.items() if k in _PARAM_FIELDS})
        self.prices = Prices(**conf.get("prezzi", {}))
        self.provider_conf = provider_conf
        self._limiters = limiters
        self._tracker = tracker
        try:
            keys = load_keys(provider_conf["chiavi"])
        except MissingKeyError as e:
            raise NeedsHuman(str(e), action=f"aggiungi {e.prefix}_ACCOUNT1=... nel .env") from None
        self._slots = [_KeySlot(k, _make_provider(provider_conf["tipo"], self.provider_name, k, provider_conf))
                       for k in keys]
        for slot in self._slots:             # già senza quota (salvato da un comando precedente): niente tentativi inutili
            slot.dead = cooldown.until(self._slot_id(slot)) is not None
        self._rr = itertools.cycle(range(len(self._slots)))
        self._lock = Lock()
        self.fallback_factory: Callable[[], Role] | None = None         # impostato dal Registry se il ruolo ha una `riserva`
        self._fallback: Role | None = None
        self.used_fallback: str | None = None  # label della riserva, se è servita

    def _slot_id(self, slot: "_KeySlot") -> str:
        return cooldown.slot_id(self.provider_name, self.model, slot.key)

    @property
    def workers(self) -> int:
        """Chiamate parallele consigliate: configurabile con `workers`, altrimenti in base al tipo di provider
        (gli endpoint OpenAI-compatibili reggono molta più concorrenza di Gemini)."""
        if self._workers_conf:
            return int(self._workers_conf)
        if self.provider_conf.get("tipo") == "openai":
            return max(1, min(self.n_keys * 4, 20))
        return max(1, min(self.n_keys, 5))

    @property
    def label(self) -> str:
        return f"{self.provider_name}/{self.model}"

    @property
    def n_keys(self) -> int:
        return len(self._slots)

    def _next_slot(self) -> _KeySlot:
        with self._lock:
            for _ in range(len(self._slots)):
                slot = self._slots[next(self._rr)]
                if not slot.dead:
                    return slot
        raise QuotaExhausted(f"Quota esaurita su tutte le chiavi di {self.provider_name} ({self.name})",
                             action=("aspetta il reset della quota giornaliera (Google: di solito alle 2:00 italiane) o aggiungi altre chiavi"
                                     if self.provider_name == "gemini" else
                                     f"ricarica il credito di {self.provider_name} (saldo insufficiente) o scegli un altro modello"))

    def complete(self, messages: list[Message], item: str | None = None,
                 validate=None, allow_truncated: bool = False, **overrides: Any) -> LLMResult:
        """Chiama il modello con retry. `validate(text) -> str|None` può rifiutare un output (motivo → ritenta).

        Una risposta troncata (finish_reason="length") è un errore: accettarla perderebbe contenuto in silenzio.
        Solo chi sa gestirla (es. la trascrizione, che divide l'audio) passa allow_truncated=True."""
        try:
            res = self._complete(messages, item, validate, allow_truncated, overrides)
        except NeedsHuman:
            if not self.fallback_factory:
                raise
            return self._use_fallback(messages, item, validate, allow_truncated, overrides)
        if (not res.ok and res.error_kind in (ErrorKind.SERVER, ErrorKind.RATE_LIMIT) and self.fallback_factory):
            # modello sovraccarico (503) o limitato dopo tutti i tentativi: per questo blocco si passa alla riserva,
            # senza metterlo da parte per sempre (al blocco dopo si riprova col principale)
            return self._use_fallback(messages, item, validate, allow_truncated, overrides)
        return res

    def _use_fallback(self, messages, item, validate, allow_truncated, overrides) -> LLMResult:
        assert self.fallback_factory is not None
        with self._lock:
            if self._fallback is None:
                self._fallback = self.fallback_factory()
            self.used_fallback = self._fallback.label
        return self._fallback.complete(messages, item=item, validate=validate,
                                       allow_truncated=allow_truncated, **overrides)

    def _complete(self, messages, item, validate, allow_truncated, overrides) -> LLMResult:
        if self._fallback is not None and all(s.dead for s in self._slots):
            raise NeedsHuman("quota esaurita (già passati alla riserva)")
        params = Params(**{**self.params.__dict__, **{k: v for k, v in overrides.items() if k in _PARAM_FIELDS}})
        last = LLMResult(error_kind=ErrorKind.OTHER, error="nessun tentativo", provider=self.provider_name,
                         model=self.model)
        for attempt in range(self.attempts):
            slot = self._next_slot()
            self._limiters.get(self.provider_name, slot.key, self.model, self.rpm).acquire()
            res = slot.client.complete(messages, self.model, params)
            res.provider, res.model = self.provider_name, self.model
            self._tracker.record(self.name, res, self.prices, item)

            if res.ok:
                reason = validate(res.text) if validate else None
                if not reason and res.finish_reason == "length" and not allow_truncated:
                    reason = f"risposta troncata al limite di {params.max_tokens or '?'} token (alza max_tokens)"
                if not reason:
                    return res
                res = LLMResult(text=res.text, usage=res.usage, error_kind=ErrorKind.OTHER,
                                error=f"output rifiutato: {reason}", provider=res.provider, model=res.model)
            last = res

            if res.error_kind == ErrorKind.AUTH:
                raise NeedsHuman(f"Chiave non valida per {self.provider_name}: {res.error}",
                                 action=f"controlla {self.provider_conf['chiavi']}_ACCOUNT* nel .env")
            if res.error_kind == ErrorKind.QUOTA:
                slot.dead = True
                cooldown.mark(self._slot_id(slot), self.label, cooldown.parse_wait(res.error),
                              cooldown.parse_raw_wait(res.error), cooldown.quota_info(res.error))
                continue
            if res.error_kind not in RETRYABLE:
                return res
            wait = (30 + 20 * attempt) if res.error_kind == ErrorKind.RATE_LIMIT else min(5 * 2 ** attempt, 60)
            time.sleep(wait * random.uniform(0.8, 1.2))
        return last


class Registry:
    def __init__(self, settings: Settings, tracker: CostTracker | None = None):
        self.settings = settings
        self.tracker = tracker or CostTracker()
        self._limiters = LimiterPool()
        self._roles: dict[str, Role] = {}

    def role(self, name: str, override: dict[str, Any] | None = None) -> Role:
        """`override` permette p.es. --notes anthropic:claude-sonnet-5-5 dalla CLI."""
        cache_key = name if not override else f"{name}:{sorted((k, str(v)) for k, v in override.items())}"
        if cache_key not in self._roles:
            conf = {**self.settings.modelli.get(name, {}), **(override or {})}
            if "provider" not in conf or "model" not in conf:
                raise ConfigError(f"[modelli.{name}] richiede provider e model")
            pconf = self.settings.providers.get(conf["provider"])
            if not pconf:
                raise ConfigError(f"Provider '{conf['provider']}' non definito in [providers]")
            role = Role(name, conf, pconf, self._limiters, self.tracker)
            if riserva := conf.get("riserva"):
                spec = parse_model_override(riserva) if isinstance(riserva, str) else dict(riserva)
                # la riserva eredita i parametri del ruolo ma non ha a sua volta una riserva (niente catene)
                role.fallback_factory = lambda spec=spec: self.role(name, {**(override or {}), **spec, "riserva": None})
            self._roles[cache_key] = role
        return self._roles[cache_key]


def parse_model_override(spec: str | None) -> dict[str, Any] | None:
    """'deepseek' → {provider}, 'anthropic:claude-sonnet-5-5' → {provider, model}."""
    if not spec:
        return None
    provider, _, model = spec.partition(":")
    return {"provider": provider, **({"model": model} if model else {})}
