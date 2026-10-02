"""Adapter OpenAI-compatibile (openai SDK): OpenAI, DeepSeek, OpenRouter, Ollama… cambiando solo base_url."""

from __future__ import annotations

import base64
from typing import Any

from sbob.llm.base import ErrorKind, FilePart, ImagePart, LLMResult, Message, Params, TextPart, Usage
from sbob.llm.errors import classify

_FINISH = {"stop": "stop", "length": "length", "content_filter": "safety"}


class OpenAICompatProvider:
    def __init__(self, name: str, key: str, conf: dict[str, Any]):
        from openai import OpenAI

        self.name = name
        self.base_url = conf.get("base_url")
        self.reasoning_effort = conf.get("reasoning_effort")
        # "deepseek": invia extra_body thinking enabled/disabled (DeepSeek V4 ragiona per default, anche quando non serve)
        self.thinking_param = conf.get("thinking_param")
        # openai.com vuole max_completion_tokens; i server compatibili in genere max_tokens
        self.max_tokens_param = conf.get("max_tokens_param",
                                         "max_completion_tokens" if not self.base_url else "max_tokens")
        self.client = OpenAI(api_key=key, base_url=self.base_url, max_retries=0)  # i retry li fa il registry

    @staticmethod
    def _content(m: Message):
        texts = [p.text for p in m.parts if isinstance(p, TextPart)]
        if len(texts) == len(m.parts):
            return "\n".join(texts)
        out: list[dict] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                out.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                b64 = base64.b64encode(p.data).decode()
                out.append({"type": "image_url", "image_url": {"url": f"data:{p.mime_type};base64,{b64}"}})
            elif isinstance(p, FilePart):
                raise ValueError("FilePart non supportato dal provider OpenAI-compatibile")
        return out

    def complete(self, messages: list[Message], model: str, params: Params) -> LLMResult:
        try:
            msgs = ([{"role": "system", "content": params.system}] if params.system else [])
            msgs += [{"role": m.role, "content": self._content(m)} for m in messages]
        except ValueError as e:
            return LLMResult(error_kind=ErrorKind.BAD_REQUEST, error=str(e))
        kw: dict[str, Any] = {"model": model, "messages": msgs, "timeout": params.timeout}
        if params.temperature is not None:
            kw["temperature"] = params.temperature
        if params.top_p is not None:
            kw["top_p"] = params.top_p
        if params.max_tokens:
            kw[self.max_tokens_param] = params.max_tokens
        if params.thinking and self.reasoning_effort:
            kw["reasoning_effort"] = self.reasoning_effort
        if self.thinking_param == "deepseek":
            kw["extra_body"] = {"thinking": {"type": "enabled" if params.thinking else "disabled"}}
            if params.thinking:              # in thinking mode temperature è ignorata, top_p vale solo in [0.95, 1]
                kw.pop("temperature", None)
        try:
            return self._parse(self.client.chat.completions.create(**kw))
        except Exception as e:  # noqa: BLE001
            return LLMResult(error_kind=classify(e), error=f"{type(e).__name__}: {e}")

    @staticmethod
    def _parse(resp) -> LLMResult:
        u = resp.usage
        details = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", None) or getattr(u, "prompt_cache_hit_tokens", None) or 0) if u else 0
        reasoning = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0 if u else 0
        usage = Usage(input_tokens=u.prompt_tokens or 0, output_tokens=max((u.completion_tokens or 0) - reasoning, 0),
                      cached_tokens=cached, thinking_tokens=reasoning) if u else Usage()
        choice = resp.choices[0]
        finish = _FINISH.get(choice.finish_reason or "", choice.finish_reason)
        text = choice.message.content
        if not text:
            return LLMResult(usage=usage, finish_reason=finish,
                             error_kind=ErrorKind.BLOCKED if finish == "safety" else ErrorKind.OTHER,
                             error=f"risposta vuota (finish_reason={choice.finish_reason})")
        return LLMResult(text=text, usage=usage, finish_reason=finish)
