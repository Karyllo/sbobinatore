import time

from sbob.llm import cooldown
from sbob.llm.base import ErrorKind, LLMResult


def test_parse_wait():
    assert cooldown.parse_wait("... Please retry in 15h10m28.9s.'") == cooldown.MAX_WAIT == 2 * 3600     # tetto di 2 ore
    assert cooldown.parse_wait("retry in 1h45m26.2s") == 1 * 3600 + 45 * 60 + 26.2                       # sotto il tetto: invariato
    assert cooldown.parse_wait("retry in 45s") == 60                                                     # minimo 1 minuto
    assert cooldown.parse_wait("402 Insufficient Balance") == cooldown.DEFAULT_WAIT and cooldown.parse_wait(None) == cooldown.DEFAULT_WAIT


def test_mark_expires_and_never_stores_the_key():
    sid = cooldown.slot_id("gemini", "m", "CHIAVE-SEGRETA")
    cooldown.mark(sid, "gemini/m", 3600)
    assert cooldown.until(sid) > time.time() and "CHIAVE-SEGRETA" not in cooldown.PATH.read_text()
    assert cooldown.status()[0]["modello"] == "gemini/m"
    cooldown.mark(sid, "gemini/m", -10)                                    # già scaduto
    assert cooldown.until(sid) is None and cooldown.status() == []


def test_role_remembers_quota_across_commands_and_skips_calls(settings, monkeypatch):
    from sbob.llm import registry as reg
    from sbob.llm.cost import CostTracker
    calls = []

    class P:
        name = "fake"
        def complete(self, messages, model, params):
            calls.append(model)
            return LLMResult(error_kind=ErrorKind.QUOTA, error="429 ... retry in 2h0m0s")
    monkeypatch.setattr(reg, "_make_provider", lambda *a: P())
    monkeypatch.setenv("FAKE_KEY_ACCOUNT1", "k1")
    from sbob.core.report import NeedsHuman
    import pytest
    role = reg.Registry(settings, CostTracker()).role("notes")
    with pytest.raises(NeedsHuman):
        role.complete([reg.Message.user("x")])
    assert calls == ["fake-1"]                                             # un solo tentativo, poi si ricorda
    calls.clear()
    role2 = reg.Registry(settings, CostTracker()).role("notes")           # "nuovo comando": stesso modello e chiave
    with pytest.raises(NeedsHuman):
        role2.complete([reg.Message.user("x")])
    assert calls == []                                                     # nessuna chiamata sprecata
