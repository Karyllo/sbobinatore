"""Adapter Gemini (google-genai)."""

from __future__ import annotations

import time
from typing import Any

from sbob.llm.base import ErrorKind, FilePart, ImagePart, LLMResult, Message, Params, TextPart, Usage
from sbob.llm.errors import classify

_SAFETY_CATEGORIES = ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                      "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
_FINISH = {"STOP": "stop", "MAX_TOKENS": "length"}


class GeminiProvider:
    def __init__(self, name: str, key: str, conf: dict[str, Any]):
        from google import genai

        self.name = name
        self.client = genai.Client(api_key=key)
        self.safety = conf.get("safety", "BLOCK_ONLY_HIGH")

    # -- file API ---------------------------------------------------------
    def _upload(self, part: FilePart, timeout: float = 300.0):
        from google.genai import types

        up = self.client.files.upload(file=str(part.path),
                                      config=types.UploadFileConfig(mime_type=part.mime_type,
                                                                    display_name=part.path.name))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.client.files.get(name=up.name).state
            if state == types.FileState.ACTIVE:
                return up
            if state == types.FileState.FAILED:
                raise RuntimeError(f"Elaborazione file fallita lato Google: {part.path.name}")
            time.sleep(3)
        raise TimeoutError(f"{part.path.name} non è diventato ACTIVE entro {timeout:.0f}s")

    def _contents(self, messages: list[Message], uploaded: list):
        from google.genai import types

        out = []
        for m in messages:
            parts = []
            for p in m.parts:
                if isinstance(p, TextPart):
                    parts.append(types.Part.from_text(text=p.text))
                elif isinstance(p, ImagePart):
                    parts.append(types.Part.from_bytes(data=p.data, mime_type=p.mime_type))
                elif isinstance(p, FilePart):
                    up = self._upload(p)
                    uploaded.append(up)
                    parts.append(types.Part.from_uri(file_uri=up.uri, mime_type=p.mime_type))
            out.append(types.Content(role="model" if m.role == "assistant" else "user", parts=parts))
        return out

    # -- chiamata -----------------------------------------------------------
    def complete(self, messages: list[Message], model: str, params: Params) -> LLMResult:
        from google.genai import types

        uploaded: list = []
        try:
            cfg: dict[str, Any] = {
                "safety_settings": [types.SafetySetting(category=c, threshold=self.safety)
                                    for c in _SAFETY_CATEGORIES],
                "http_options": types.HttpOptions(timeout=int(params.timeout * 1000)),
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            }
            if params.temperature is not None:
                cfg["temperature"] = params.temperature
            if params.top_p is not None:
                cfg["top_p"] = params.top_p
            if params.max_tokens:
                cfg["max_output_tokens"] = params.max_tokens
            if params.system:
                cfg["system_instruction"] = params.system
            if params.thinking:
                cfg["thinking_config"] = types.ThinkingConfig(include_thoughts=False, thinking_level="HIGH")

            resp = self.client.models.generate_content(
                model=model, contents=self._contents(messages, uploaded),
                config=types.GenerateContentConfig(**cfg))
            return self._parse(resp)
        except Exception as e:  # noqa: BLE001 — il contratto è: mai eccezioni
            return LLMResult(error_kind=classify(e), error=f"{type(e).__name__}: {e}")
        finally:
            for up in uploaded:
                try:
                    self.client.files.delete(name=up.name)
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _parse(resp) -> LLMResult:
        u = getattr(resp, "usage_metadata", None)
        usage = Usage(
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            output_tokens=(getattr(u, "candidates_token_count", None) or getattr(u, "response_token_count", None) or 0),
            cached_tokens=getattr(u, "cached_content_token_count", 0) or 0,
            thinking_tokens=getattr(u, "thoughts_token_count", 0) or 0,
        ) if u else Usage()
        cand = (resp.candidates or [None])[0]
        reason_raw = getattr(getattr(cand, "finish_reason", None), "name", None) or str(getattr(cand, "finish_reason", ""))
        finish = _FINISH.get(reason_raw, "safety" if reason_raw and reason_raw not in ("FINISH_REASON_UNSPECIFIED", "None", "") else None)
        text = resp.text
        if not text:
            blocked = finish == "safety" or getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            return LLMResult(usage=usage, finish_reason=finish,
                             error_kind=ErrorKind.BLOCKED if blocked else ErrorKind.OTHER,
                             error=f"risposta vuota (finish_reason={reason_raw or 'n/d'})")
        return LLMResult(text=text, usage=usage, finish_reason=finish)
