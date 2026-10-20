"""Provider-agnostic model client (monitor_spec.md §1, §5).

The API key comes only from ``APART_API_KEY``. It is never logged, stored in a
record, or written to ``repr``. Tests use ``FakeClient`` and make no calls.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


class ApiError(RuntimeError):
    """Raised after the configured API attempts are exhausted."""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    response_id: Optional[str]
    model: str
    snapshot: str
    provider_seed: Optional[int]


class ModelClient:
    """Minimal OpenAI-compatible chat client.

    Endpoint/model/timeout come from ``APART_BASE_URL``, ``APART_MODEL``,
    ``APART_MODEL_SNAPSHOT`` and ``APART_TIMEOUT_S``. Retries: exponential
    backoff, 4 attempts (§5.5), then ``ApiError``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        snapshot: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_attempts: int = 4,
        backoff_base_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("APART_API_KEY")
        if not key:
            raise RuntimeError(
                "APART_API_KEY is not set; keys are read from the environment only"
            )
        self._api_key = key
        self.base_url = (
            base_url or os.environ.get("APART_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = model or os.environ.get("APART_MODEL") or "gpt-4o-mini"
        self.snapshot = snapshot or os.environ.get("APART_MODEL_SNAPSHOT") or self.model
        self.timeout_s = float(
            timeout_s if timeout_s is not None else os.environ.get("APART_TIMEOUT_S", "60")
        )
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep

    def __repr__(self) -> str:  # never expose the key
        return (
            f"ModelClient(model={self.model!r}, base_url={self.base_url!r}, "
            "api_key=<redacted>)"
        )

    def call_model(
        self,
        messages: Sequence[dict],
        temperature: float,
        seed: Optional[int] = None,
        max_tokens: int = 250,
    ) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = seed
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    data = json.loads(response.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"]
                return ModelResponse(
                    text=text,
                    response_id=data.get("id"),
                    model=self.model,
                    snapshot=self.snapshot,
                    provider_seed=seed,
                )
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
        raise ApiError(f"model call failed after {self.max_attempts} attempts") from last_error


def dry_run_response(messages: Sequence[dict]) -> str:
    """Valid JSON for every view; no network, deterministic."""
    user = messages[-1]["content"] if messages else ""
    if "CHECKLIST" in user:
        payload = {
            "checklist": {
                "messages": [],
                "speaker_match": [],
                "receiver_compliance": [],
            },
            "rationale": "dry run",
            "score": 0,
        }
    else:
        payload = {"rationale": "dry run", "score": 0}
    return json.dumps(payload)


class FakeClient:
    """In-memory client for dry runs and tests."""

    def __init__(
        self,
        responder: Optional[Callable[[Sequence[dict], float, Optional[int], int], str]] = None,
        model: str = "fake-model",
        snapshot: str = "dry-run",
    ) -> None:
        self._responder = responder or (
            lambda messages, temperature, seed, index: dry_run_response(messages)
        )
        self.model = model
        self.snapshot = snapshot
        self.calls: list[dict] = []

    def call_model(
        self,
        messages: Sequence[dict],
        temperature: float,
        seed: Optional[int] = None,
        max_tokens: int = 250,
    ) -> ModelResponse:
        index = len(self.calls)
        self.calls.append(
            {
                "messages": list(messages),
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_tokens,
            }
        )
        text = self._responder(messages, temperature, seed, index)
        return ModelResponse(
            text=text,
            response_id=f"fake-{index + 1}",
            model=self.model,
            snapshot=self.snapshot,
            provider_seed=seed,
        )


class ScriptedClient(FakeClient):
    """FakeClient returning a fixed sequence of replies (tests)."""

    def __init__(self, texts: Sequence[str], **kwargs) -> None:
        replies = iter(texts)
        super().__init__(responder=lambda *_: next(replies), **kwargs)
