"""Il registry gestisce retry, rotazione chiavi e costi senza dipendere da un SDK reale."""

import pytest

from sbob.core.report import NeedsHuman
from sbob.llm import registry as reg_mod
from sbob.llm.base import ErrorKind, LLMResult, Message, Usage
from sbob.llm.cost import CostTracker


class FakeProvider:
    script: list = []   # sequenza di risultati condivisa tra le istanze
    calls: list = []

    def __init__(self, name, key, conf):
        self.name, self.key = name, key

    def complete(self, messages, model, params):
        FakeProvider.calls.append(self.key)
        return FakeProvider.script.pop(0)


@pytest.fixture
def registry(settings, monkeypatch):
    monkeypatch.setattr(reg_mod, "_make_provider", lambda tipo, name, key, conf: FakeProvider(name, key, conf))
    monkeypatch.setattr(reg_mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("FAKE_KEY_ACCOUNT1", "k1")
    monkeypatch.setenv("FAKE_KEY_ACCOUNT2", "k2")
    FakeProvider.calls = []
    return reg_mod.Registry(settings, CostTracker())


def ok(text="ciao\n" * 10, inp=100, out=50):
    return LLMResult(text=text, usage=Usage(input_tokens=inp, output_tokens=out))


def test_retry_then_success_and_cost(registry):
    FakeProvider.script = [LLMResult(error_kind=ErrorKind.RATE_LIMIT, error="429"), ok()]
    role = registry.role("notes", {"prezzi": {"input": 1.0, "output": 2.0}})
    r = role.complete([Message.user("x")])
    assert r.ok and r.provider == "fake" and r.model == "fake-1"
    assert FakeProvider.calls == ["k1", "k2"]            # round-robin sulle chiavi
    s = registry.tracker.summary()
    assert s["per_modello"]["notes:fake/fake-1"]["calls"] == 2
    assert s["usd_totale"] == pytest.approx((100 * 1 + 50 * 2) / 1e6, rel=1e-3)


def test_quota_kills_key_then_needs_human(registry):
    FakeProvider.script = [LLMResult(error_kind=ErrorKind.QUOTA)] * 2
    with pytest.raises(NeedsHuman):
        registry.role("notes").complete([Message.user("x")])


def test_validate_rejects_wall_of_text(registry):
    FakeProvider.script = [ok(text="a" * 600), ok()]
    wall = lambda t: "muro di testo" if len(t) > 500 and t.count("\n") < 5 else None  # noqa: E731
    r = registry.role("notes").complete([Message.user("x")], validate=wall)
    assert r.ok and r.text.startswith("ciao")


def test_non_retryable_returns_immediately(registry):
    FakeProvider.script = [LLMResult(error_kind=ErrorKind.BAD_REQUEST, error="modello inesistente")]
    r = registry.role("notes").complete([Message.user("x")])
    assert not r.ok and len(FakeProvider.calls) == 1


def test_missing_keys_is_human(settings, monkeypatch):
    monkeypatch.delenv("FAKE_KEY_ACCOUNT1", raising=False)
    with pytest.raises(NeedsHuman):
        reg_mod.Registry(settings).role("notes")


def test_parse_override():
    assert reg_mod.parse_model_override("deepseek") == {"provider": "deepseek"}
    assert reg_mod.parse_model_override("anthropic:claude-sonnet-5-5") == {
        "provider": "anthropic", "model": "claude-sonnet-5-5"}


def test_truncated_is_rejected_unless_allowed(registry):
    trunc = LLMResult(text="mezza risposta\n" * 10, finish_reason="length")
    FakeProvider.script = [trunc, trunc, trunc]
    r = registry.role("notes").complete([Message.user("x")])
    assert not r.ok and "troncata" in r.error
    FakeProvider.script = [trunc]
    assert registry.role("notes").complete([Message.user("x")], allow_truncated=True).ok


def test_fallback_when_all_keys_out_of_quota(settings, monkeypatch):
    """Principale (fake, 2 chiavi) in quota giornaliera → la riserva (stesso provider finto, altro modello) risponde."""
    monkeypatch.setattr(reg_mod, "_make_provider", lambda tipo, name, key, conf: FakeProvider(name, key, conf))
    monkeypatch.setattr(reg_mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("FAKE_KEY_ACCOUNT1", "k1")
    monkeypatch.setenv("FAKE_KEY_ACCOUNT2", "k2")
    FakeProvider.calls = []
    reg = reg_mod.Registry(settings, CostTracker())
    role = reg.role("notes", {"riserva": {"provider": "fake", "model": "riserva-1", "thinking": False}})
    FakeProvider.script = [LLMResult(error_kind=ErrorKind.QUOTA)] * 2 + [ok(), ok()]
    r = role.complete([Message.user("x")])
    assert r.ok and r.model == "riserva-1" and role.used_fallback == "fake/riserva-1"
    r2 = role.complete([Message.user("y")])               # chiamate successive vanno dritte alla riserva
    assert r2.model == "riserva-1" and len(FakeProvider.calls) == 4
