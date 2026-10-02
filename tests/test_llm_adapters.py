"""Adapter con client SDK finti: si verifica mappatura errori, parametri inviati e parsing dell'usage."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from sbob.llm.anthropic import AnthropicProvider
from sbob.llm.base import ErrorKind, FilePart, ImagePart, Message, Params
from sbob.llm.errors import classify
from sbob.llm.gemini import GeminiProvider
from sbob.llm.openai_compat import OpenAICompatProvider


class HttpError(Exception):
    def __init__(self, status, msg=""):
        super().__init__(msg)
        self.status_code = status


class GoogleError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.code = code


@pytest.mark.parametrize("exc,kind", [
    (HttpError(429, "slow down"), ErrorKind.RATE_LIMIT),
    (GoogleError(429, "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel"), ErrorKind.QUOTA),
    (GoogleError(429, "RESOURCE_EXHAUSTED. You exceeded your current quota. quotaId: "
                      "GenerateContentInputTokensPerModelPerMinute-FreeTier"), ErrorKind.RATE_LIMIT),
    (HttpError(429, "insufficient_quota: You exceeded your current quota"), ErrorKind.QUOTA),
    (GoogleError(429, "You exceeded your current quota, please check your plan and billing details. "
                      "{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}"), ErrorKind.RATE_LIMIT),
    (GoogleError(429, "You exceeded your current quota, please check your plan and billing details. "
                      "{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}"),
     ErrorKind.QUOTA),
    (HttpError(402, "Insufficient Balance"), ErrorKind.QUOTA),
    (HttpError(401), ErrorKind.AUTH),
    (GoogleError(403), ErrorKind.AUTH),
    (HttpError(503), ErrorKind.SERVER),
    (HttpError(400, "too long"), ErrorKind.BAD_REQUEST),
    (type("APITimeoutError", (Exception,), {})("t"), ErrorKind.SERVER),
    (ValueError("boh"), ErrorKind.OTHER),
])
def test_classify(exc, kind):
    assert classify(exc) == kind


# ------------------------------------------------------------------ openai-compat
def _oa_resp(text="ciao", finish="stop", pt=10, ct=5, cached=0, reasoning=0):
    return NS(choices=[NS(message=NS(content=text), finish_reason=finish)],
              usage=NS(prompt_tokens=pt, completion_tokens=ct, prompt_tokens_details=NS(cached_tokens=cached),
                       completion_tokens_details=NS(reasoning_tokens=reasoning)))


def test_openai_params_and_usage():
    p = OpenAICompatProvider("deepseek", "k", {"base_url": "https://api.deepseek.com"})
    seen = {}
    p.client = NS(chat=NS(completions=NS(create=lambda **kw: seen.update(kw) or _oa_resp(cached=4, reasoning=2, ct=9))))
    r = p.complete([Message.user("hi")], "m", Params(temperature=0.3, top_p=0.9, max_tokens=100, system="sys"))
    assert r.ok and r.usage.cached_tokens == 4 and r.usage.thinking_tokens == 2 and r.usage.output_tokens == 7
    assert seen["max_tokens"] == 100 and "max_completion_tokens" not in seen   # server compatibile
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_official_uses_max_completion_tokens_and_length_is_not_error():
    p = OpenAICompatProvider("openai", "k", {})
    seen = {}
    p.client = NS(chat=NS(completions=NS(create=lambda **kw: seen.update(kw) or _oa_resp(finish="length"))))
    r = p.complete([Message.user("hi")], "m", Params(max_tokens=50))
    assert seen["max_completion_tokens"] == 50 and r.ok and r.finish_reason == "length"


def test_openai_errors_and_filepart():
    p = OpenAICompatProvider("x", "k", {})

    def boom(**kw):
        raise HttpError(429, "rate")
    p.client = NS(chat=NS(completions=NS(create=boom)))
    assert p.complete([Message.user("hi")], "m", Params()).error_kind == ErrorKind.RATE_LIMIT
    r = p.complete([Message.user(FilePart(Path("a.mp3"), "audio/mpeg"))], "m", Params())
    assert r.error_kind == ErrorKind.BAD_REQUEST


# ------------------------------------------------------------------ anthropic
class _Stream:
    def __init__(self, msg): self.msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.msg


def _an_msg(text="ciao", stop="end_turn"):
    return NS(content=[NS(type="thinking", text="x"), NS(type="text", text=text)], stop_reason=stop,
              usage=NS(input_tokens=10, output_tokens=20, cache_read_input_tokens=5))


def test_anthropic_thinking_drops_sampling_and_parses_usage():
    p = AnthropicProvider("anthropic", "k", {})
    seen = {}
    p.client = NS(messages=NS(stream=lambda **kw: seen.update(kw) or _Stream(_an_msg())))
    r = p.complete([Message.user("hi")], "m", Params(temperature=0.3, top_p=0.9, max_tokens=65536, thinking=True,
                                                    system="sys"))
    assert seen["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert "temperature" not in seen and "top_p" not in seen and seen["system"] == "sys"
    assert r.text == "ciao" and r.usage.input_tokens == 15 and r.usage.cached_tokens == 5


def test_anthropic_plain_and_refusal_and_audio():
    p = AnthropicProvider("anthropic", "k", {})
    seen = {}
    p.client = NS(messages=NS(stream=lambda **kw: seen.update(kw) or _Stream(_an_msg(text="", stop="refusal"))))
    r = p.complete([Message.user("hi")], "m", Params(temperature=0.2))
    assert seen["temperature"] == 0.2 and seen["max_tokens"] == 8192
    assert r.error_kind == ErrorKind.BLOCKED
    r = p.complete([Message.user(FilePart(Path("a.mp3"), "audio/mpeg"))], "m", Params())
    assert r.error_kind == ErrorKind.BAD_REQUEST


# ------------------------------------------------------------------ gemini
def _g_resp(text="ciao", finish="STOP"):
    return NS(text=text, candidates=[NS(finish_reason=NS(name=finish))], prompt_feedback=None,
              usage_metadata=NS(prompt_token_count=11, candidates_token_count=22, cached_content_token_count=3,
                                thoughts_token_count=7, total_token_count=40))


def test_gemini_config_and_usage():
    p = GeminiProvider("gemini", "k", {})
    seen = {}
    p.client = NS(models=NS(generate_content=lambda **kw: seen.update(kw) or _g_resp()))
    r = p.complete([Message.user("hi", ImagePart(b"x"))], "gem", Params(temperature=0.3, max_tokens=99, thinking=True,
                                                                     system="sys"))
    cfg = seen["config"]
    assert cfg.max_output_tokens == 99 and cfg.system_instruction == "sys"
    assert cfg.thinking_config.thinking_level.name == "HIGH" and len(cfg.safety_settings) == 4
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.thinking_tokens, r.usage.cached_tokens) == (11, 22, 7, 3)
    assert r.finish_reason == "stop"


def test_gemini_blocked_and_length_and_error():
    p = GeminiProvider("gemini", "k", {})
    p.client = NS(models=NS(generate_content=lambda **kw: _g_resp(text=None, finish="SAFETY")))
    assert p.complete([Message.user("hi")], "m", Params()).error_kind == ErrorKind.BLOCKED
    p.client = NS(models=NS(generate_content=lambda **kw: _g_resp(finish="MAX_TOKENS")))
    r = p.complete([Message.user("hi")], "m", Params())
    assert r.ok and r.finish_reason == "length"

    def boom(**kw):
        raise GoogleError(429, "RESOURCE_EXHAUSTED per day")
    p.client = NS(models=NS(generate_content=boom))
    assert p.complete([Message.user("hi")], "m", Params()).error_kind == ErrorKind.QUOTA


def test_gemini_filepart_is_uploaded_and_always_deleted(tmp_path):
    from google.genai import types

    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    p = GeminiProvider("gemini", "k", {})
    deleted = []
    files = NS(upload=lambda **kw: NS(name="files/1", uri="gs://f"),
               get=lambda name: NS(state=types.FileState.ACTIVE),
               delete=lambda name: deleted.append(name))

    def gen(**kw):
        raise GoogleError(500, "boom")
    p.client = NS(files=files, models=NS(generate_content=gen))
    r = p.complete([Message.user(FilePart(audio, "audio/mp4"), "trascrivi")], "m", Params())
    assert r.error_kind == ErrorKind.SERVER and deleted == ["files/1"]


def test_deepseek_thinking_explicitly_toggled():
    p = OpenAICompatProvider("deepseek", "k", {"base_url": "https://api.deepseek.com", "thinking_param": "deepseek"})
    seen = {}
    p.client = NS(chat=NS(completions=NS(create=lambda **kw: seen.update(kw) or _oa_resp())))
    p.complete([Message.user("hi")], "m", Params(temperature=0.1))
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}} and seen["temperature"] == 0.1
    seen.clear()
    p.complete([Message.user("hi")], "m", Params(temperature=0.3, top_p=0.95, thinking=True))
    assert seen["extra_body"] == {"thinking": {"type": "enabled"}} and "temperature" not in seen
