"""Extractor client boundary (extension_spec.md §6.1, §16.8, §16.9.6 step 3).

Two clients share one contract, ``complete(message_text, call_id)``:

* ``FakeExtractorClient`` — offline and scripted; no network, no ledger.
* ``GuardedExtractorClient`` — the real one. Every request is dispatched through a
  ``monitors.client.ModelClient`` bound to a ``LedgerGuard``, so the worst-case
  cost is reserved durably *before* the request goes out and settled from
  provider-reported usage afterwards. No code path here sends a request without a
  reservation, retries automatically, or falls back to another model or to an
  unguarded client.

The extractor sees the message text only: never actions, plans, notes, briefs,
variant, condition, historical class, outcome, or monitor output.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from extraction.prompts import (EXTRACTOR_PROMPT_SHA256, GAME_CONTEXT_SHA256,
                                extractor_message, extractor_system)

# One extractor reply is a small JSON object (claims + attempt fields + note).
EXTRACTOR_MAX_TOKENS = 400
EXTRACTOR_TEMPERATURE = 0.0  # deterministic annotation (§16.8); not a research knob

USAGE_REPORTED = "reported"
USAGE_UNKNOWN = "unknown"
STATUS_OK = "ok"
STATUS_MALFORMED = "malformed_response"
STATUS_API_ERROR = "api_error"

_HTTP_STATUS = re.compile(r"^HTTP (\d{3})")


class ExtractorError(RuntimeError):
    """The extractor is misconfigured or produced no usable response."""



@dataclass(frozen=True)
class ExtractorRequest:
    system: str
    user: str
    prompt_sha256: str = EXTRACTOR_PROMPT_SHA256
    game_context_sha256: str = GAME_CONTEXT_SHA256

    def messages(self) -> list[dict]:
        return [{"role": "system", "content": self.system},
                {"role": "user", "content": self.user}]


def build_extractor_request(message_text: str) -> ExtractorRequest:
    """The only episode-specific content is the JSON-quoted message text."""
    return ExtractorRequest(system=extractor_system(), user=extractor_message(message_text))


class FakeExtractorClient:
    """Records requests and returns a scripted response. Offline only."""

    model = "fake-extractor"

    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[ExtractorRequest] = []

    def complete(self, message_text: str, call_id: Optional[str] = None) -> str:
        # ``call_id`` is accepted for signature parity with the guarded client and
        # is deliberately ignored: the fake makes no request and books no spend.
        self.requests.append(build_extractor_request(message_text))
        return self.response


def _strip_code_fence(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return body.strip()


def parse_extraction(text: str) -> dict:
    """Parse one extractor reply into a dict. Fails closed; never invents fields.

    Anything that is not a JSON object with a ``claims`` list is unusable: the
    caller records ``malformed_response`` and the message stays unannotated.
    """
    if not isinstance(text, str) or not text.strip():
        raise ExtractorError("empty extractor response")
    body = _strip_code_fence(text)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ExtractorError("extractor response contains no JSON object")
    try:
        data = json.loads(body[start:end + 1])
    except ValueError as exc:
        raise ExtractorError("extractor response is not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ExtractorError("extractor response has no 'claims' list")
    return data


def _failure_category(exc: BaseException) -> str:
    """A stable, non-revealing failure label. Provider bodies are never kept.

    ``ApiError`` embeds up to 300 characters of the provider response body, and
    ``ModelClient`` re-raises a second ``ApiError`` ("model call failed after N
    attempts") chained to it, so the chain is walked for an ``HTTP <code>``
    prefix. Only the status code is kept; the text never is.
    """
    name = type(exc).__name__
    current: Optional[BaseException] = exc
    for _ in range(5):
        if current is None:
            break
        match = _HTTP_STATUS.match(str(current))
        if match:
            return f"http_{match.group(1)}"
        current = current.__cause__ or current.__context__
    return name


def _usage_tokens(usage) -> Optional[tuple]:
    """(in, out) token counts, or None when missing or malformed.

    Mirrors ``monitors.ledger``'s rule so the extractor record and the ledger
    agree on what counts as usable usage. Accepts OpenAI- and Anthropic-style keys.
    """
    if not isinstance(usage, Mapping):
        return None
    tokens_in = usage.get("prompt_tokens", usage.get("input_tokens"))
    tokens_out = usage.get("completion_tokens", usage.get("output_tokens"))
    for value in (tokens_in, tokens_out):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
    return tokens_in, tokens_out


@dataclass(frozen=True)
class ExtractorCall:
    """Everything needed to reconstruct one extractor call's lifecycle."""

    call_id: str
    model: str
    status: str
    prompt_sha256: str
    response_id: Optional[str] = None
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    usage: Optional[dict] = None
    usage_status: str = USAGE_UNKNOWN
    text: Optional[str] = None
    parsed: Optional[dict] = None
    failure_category: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "model": self.model,
            "response_id": self.response_id,
            "served_model": self.served_model,
            "served_provider": self.served_provider,
            "usage": self.usage,
            "usage_status": self.usage_status,
            "prompt_sha256": self.prompt_sha256,
            "status": self.status,
            "failure_category": self.failure_category,
        }


class GuardedExtractorClient:
    """The real extractor: guarded, single-attempt, no fallback, usage verbatim.

    Construction fails closed unless it is handed a ``ModelClient`` that carries a
    ``LedgerGuard`` *and* is configured for exactly one transport attempt, so no
    call this object makes can be sent unguarded or retried automatically.
    """

    def __init__(self, client, *, label: str = "E1",
                 max_tokens: int = EXTRACTOR_MAX_TOKENS,
                 temperature: float = EXTRACTOR_TEMPERATURE) -> None:
        from monitors.client import ModelClient  # local: keeps the import surface small

        if not isinstance(client, ModelClient):
            raise ExtractorError(
                "a monitors.client.ModelClient is required; refusing an unguarded client"
            )
        if getattr(client, "guard", None) is None:
            raise ExtractorError("the client has no LedgerGuard; refusing to send")
        if client.max_attempts != 1:
            raise ExtractorError(
                f"max_attempts must be 1 (got {client.max_attempts}); "
                "the extractor never retries"
            )
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ExtractorError("max_tokens must be a positive integer")
        self._client = client
        self.label = label
        self.model = client.model
        self.max_tokens = max_tokens
        self.temperature = float(temperature)
        self.calls: list[ExtractorCall] = []

    def __repr__(self) -> str:  # never exposes the key or the guard
        return (f"GuardedExtractorClient(label={self.label!r}, model={self.model!r}, "
                f"max_tokens={self.max_tokens}, calls={len(self.calls)})")

    # -- contract -----------------------------------------------------------

    def complete(self, message_text: str, call_id: Optional[str] = None) -> str:
        """Return the raw reply text. ``call_id`` is mandatory for a real call."""
        return self.call(message_text, call_id).text or ""

    def call(self, message_text: str, call_id: Optional[str] = None) -> ExtractorCall:
        """One guarded request. Raises on transport failure; never retries."""
        if not isinstance(call_id, str) or not call_id.strip():
            raise ExtractorError("call_id is required for a guarded extractor request")
        request = build_extractor_request(message_text)
        prompt_sha256 = hashlib.sha256(
            (request.system + "\x00" + request.user).encode("utf-8")
        ).hexdigest()
        try:
            # Reservation, dispatch marker and settlement all happen inside the
            # guarded client. Any ledger error propagates: nothing is invented and
            # no request is sent on top of a failed booking.
            response = self._client.call_model(
                request.messages(), temperature=self.temperature,
                max_tokens=self.max_tokens, call_id=call_id,
            )
        except Exception as exc:  # transport / HTTP / ledger — record, then stop the caller
            self.calls.append(self._record(call_id, prompt_sha256, None, STATUS_API_ERROR,
                                           failure_category=_failure_category(exc)))
            raise
        text = getattr(response, "text", None)
        try:
            parsed = parse_extraction(text if isinstance(text, str) else "")
        except ExtractorError:
            record = self._record(call_id, prompt_sha256, response, STATUS_MALFORMED, text=text)
        else:
            record = self._record(call_id, prompt_sha256, response, STATUS_OK,
                                  text=text, parsed=parsed)
        self.calls.append(record)
        return record

    # -- helpers ------------------------------------------------------------

    def _record(self, call_id: str, prompt_sha256: str, response, status: str,
                text: Optional[str] = None, parsed: Optional[dict] = None,
                failure_category: Optional[str] = None) -> ExtractorCall:
        raw_usage = getattr(response, "usage", None) if response is not None else None
        usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else None
        return ExtractorCall(
            call_id=call_id,
            model=self.model,
            status=status,
            prompt_sha256=prompt_sha256,
            response_id=getattr(response, "response_id", None) if response is not None else None,
            served_model=getattr(response, "served_model", None) if response is not None else None,
            served_provider=(getattr(response, "served_provider", None)
                             if response is not None else None),
            # The provider value is kept verbatim as evidence, but the status
            # records whether it was actually usable for pricing. Missing or
            # malformed usage is "unknown"; it is never replaced by a zero or a guess.
            usage=usage,
            usage_status=(USAGE_REPORTED if _usage_tokens(raw_usage) is not None
                          else USAGE_UNKNOWN),
            text=text,
            parsed=parsed,
            failure_category=failure_category,
        )


