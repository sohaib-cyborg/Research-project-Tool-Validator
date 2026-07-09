"""
SCADS.ai LLM client wrapper.

A thin, robust layer over the OpenAI-compatible SCADS endpoint. Both consumers
build on this:
  * test generation  -> complete_json() (a list of test cases)
  * rubber-duck       -> complete_text() (free-form reasoning) then
                         complete_json() (a filled yes/no rubric)

Features:
  * retry with exponential backoff on transient errors (429 / timeout / 5xx)
  * a text method and a JSON method (the JSON method re-asks if output isn't
    valid JSON)
  * returns a typed LLMResult carrying token counts (for later cost metrics)
  * NO caching yet -- deliberately deferred (in-memory dedup + disk-freeze for
    reproducibility will be added with test generation, keyed by tool hash).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import openai

from . import config


@dataclass
class LLMResult:
    """Everything a caller might need from one completion."""
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: int = 1
    raw: Any = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# Errors worth retrying (transient). Import defensively across openai versions.
_RETRYABLE = tuple(
    e for e in (
        getattr(openai, "RateLimitError", None),
        getattr(openai, "APITimeoutError", None),
        getattr(openai, "APIConnectionError", None),
        getattr(openai, "InternalServerError", None),
    ) if e is not None
)


class ScadsClient:
    def __init__(self, model: str | None = None):
        self.model = model or config.DEFAULT_MODEL
        self._client = openai.OpenAI(
            base_url=config.BASE_URL,
            api_key=config.api_key(),
            timeout=config.REQUEST_TIMEOUT_S,
        )
        # Async client is created lazily so purely-sync callers don't pay for it.
        self._aclient: openai.AsyncOpenAI | None = None

    def _async(self) -> openai.AsyncOpenAI:
        if self._aclient is None:
            self._aclient = openai.AsyncOpenAI(
                base_url=config.BASE_URL,
                api_key=config.api_key(),
                timeout=config.REQUEST_TIMEOUT_S,
            )
        return self._aclient

    # -- core call with retry/backoff --------------------------------------
    def _call(self, messages: list[dict], **kw) -> LLMResult:
        last_err = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=kw.get("max_tokens", config.DEFAULT_MAX_TOKENS),
                    temperature=kw.get("temperature", config.DEFAULT_TEMPERATURE),
                    **({"response_format": kw["response_format"]}
                       if "response_format" in kw else {}),
                )
                usage = getattr(resp, "usage", None)
                return LLMResult(
                    content=resp.choices[0].message.content or "",
                    model=self.model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    attempts=attempt,
                    raw=resp,
                )
            except _RETRYABLE as e:          # transient -> back off and retry
                last_err = e
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue
        raise RuntimeError(
            f"SCADS call failed after {config.MAX_RETRIES} attempts: {last_err}"
        )

    # -- ASYNC twin of _call ----------------------------------------------
    async def _acall(self, messages: list[dict], **kw) -> LLMResult:
        last_err = None
        aclient = self._async()
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                resp = await aclient.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=kw.get("max_tokens", config.DEFAULT_MAX_TOKENS),
                    temperature=kw.get("temperature", config.DEFAULT_TEMPERATURE),
                    **({"response_format": kw["response_format"]}
                       if "response_format" in kw else {}),
                )
                usage = getattr(resp, "usage", None)
                return LLMResult(
                    content=resp.choices[0].message.content or "",
                    model=self.model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    attempts=attempt,
                    raw=resp,
                )
            except _RETRYABLE as e:
                last_err = e
                if attempt < config.MAX_RETRIES:
                    await asyncio.sleep(config.BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue
        raise RuntimeError(
            f"SCADS call failed after {config.MAX_RETRIES} attempts: {last_err}"
        )

    # -- public: free-form text --------------------------------------------
    def complete_text(self, prompt: str, *, system: str | None = None, **kw) -> LLMResult:
        messages = ([{"role": "system", "content": system}] if system else []) \
                   + [{"role": "user", "content": prompt}]
        return self._call(messages, **kw)

    async def acomplete_text(self, prompt: str, *, system: str | None = None, **kw) -> LLMResult:
        """Async twin of complete_text — needed for parallel voter calls."""
        messages = ([{"role": "system", "content": system}] if system else []) \
                   + [{"role": "user", "content": prompt}]
        return await self._acall(messages, **kw)

    # -- public: structured JSON -------------------------------------------
    def complete_json(self, prompt: str, *, system: str | None = None,
                      max_json_retries: int = 2, **kw) -> tuple[dict | list, LLMResult]:
        """Ask for JSON and parse it. Re-asks (once or twice) if the model
        returns something that doesn't parse. Returns (parsed, LLMResult)."""
        sys = (system or "") + (
            "\nRespond with ONLY valid JSON. No prose, no markdown fences."
        )
        result = None
        for _ in range(max_json_retries + 1):
            result = self._call(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, **kw,
            )
            parsed = _try_parse_json(result.content)
            if parsed is not None:
                return parsed, result
            # nudge and retry
            prompt = prompt + "\n\nYour previous reply was not valid JSON. Return ONLY valid JSON."
        raise ValueError(
            f"Model did not return valid JSON after retries. Last content:\n{result.content[:300]}"
        )

    async def acomplete_json(self, prompt: str, *, system: str | None = None,
                             max_json_retries: int = 2, **kw) -> tuple[dict | list, LLMResult]:
        """Async twin of complete_json — used by parallel voter calls."""
        sys = (system or "") + (
            "\nRespond with ONLY valid JSON. No prose, no markdown fences."
        )
        result = None
        for _ in range(max_json_retries + 1):
            result = await self._acall(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, **kw,
            )
            parsed = _try_parse_json(result.content)
            if parsed is not None:
                return parsed, result
            prompt = prompt + "\n\nYour previous reply was not valid JSON. Return ONLY valid JSON."
        raise ValueError(
            f"Model did not return valid JSON after retries. Last content:\n{result.content[:300]}"
        )


def _try_parse_json(text: str):
    """Best-effort JSON parse: strip common markdown fences, then load."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        return json.loads(t.strip())
    except (json.JSONDecodeError, ValueError):
        return None
