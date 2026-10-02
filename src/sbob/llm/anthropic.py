"""Adapter Anthropic (anthropic SDK). Usa lo streaming: è richiesto dall'SDK per max_tokens elevati."""

from __future__ import annotations

import base64
from typing import Any

from sbob.llm.base import ErrorKind, FilePart, ImagePart, LLMResult, Message, Params, TextPart, Usage
from sbob.llm.errors import classify

_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "safety"}
DEFAULT_MAX_TOKENS = 8192


class AnthropicProvider:
    def __init__(self, name: str, key: str, conf: dict[str, Any]):
        import anthropic

        self.name = name
        self.client = anthropic.Anthropic(api_key=key, max_retries=0)  # i retry li fa il registry

    @staticmethod
    def _content(m: Message) -> list[dict]:
        out: list[dict] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                out.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                out.append({"type": "image", "source": {"type": "base64", "media_type": p.mime_type,
                                                        "data": base64.b64encode(p.data).decode()}})
            elif isinstance(p, FilePart):
                if p.mime_type != "application/pdf":
                    raise ValueError(f"FilePart {p.mime_type} non supportato da Anthropic (solo PDF)")
                out.append({"type": "document", "source": {"type": "base64", "media_type": p.mime_type,
                                                           "data": base64.b64encode(p.path.read_bytes()).decode()}})
        return out

    def complete(self, messages: list[Message], model: str, params: Params) -> LLMResult:
        try:
            msgs = [{"role": m.role, "content": self._content(m)} for m in messages]
        except ValueError as e:
            return LLMResult(error_kind=ErrorKind.BAD_REQUEST, error=str(e))
        max_tokens = params.max_tokens or DEFAULT_MAX_TOKENS
        kw: dict[str, Any] = {"model": model, "messages": msgs, "max_tokens": max_tokens, "timeout": params.timeout}
        if params.system:
            kw["system"] = params.system
        if params.thinking:
            # con thinking esteso temperature/top_p non sono ammessi; il budget deve stare sotto max_tokens
            budget = min(16000, max_tokens // 2)
            if budget >= 1024:
                kw["thinking"] = {"type": "enabled", "budget_tokens": budget}
        if "thinking" not in kw:
            if params.temperature is not None:
                kw["temperature"] = params.temperature
            if params.top_p is not None and params.temperature is None:
                kw["top_p"] = params.top_p
        try:
            with self.client.messages.stream(**kw) as stream:
                return self._parse(stream.get_final_message())
        except Exception as e:  # noqa: BLE001
            return LLMResult(error_kind=classify(e), error=f"{type(e).__name__}: {e}")

    @staticmethod
    def _parse(msg) -> LLMResult:
        u = msg.usage
        # i token di thinking sono già dentro output_tokens
        usage = Usage(input_tokens=(u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0),
                      output_tokens=u.output_tokens or 0,
                      cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)
        finish = _FINISH.get(msg.stop_reason or "", msg.stop_reason)
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        if not text:
            return LLMResult(usage=usage, finish_reason=finish,
                             error_kind=ErrorKind.BLOCKED if finish == "safety" else ErrorKind.OTHER,
                             error=f"risposta vuota (stop_reason={msg.stop_reason})")
        return LLMResult(text=text, usage=usage, finish_reason=finish)
