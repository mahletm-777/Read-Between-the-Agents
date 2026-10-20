"""Provider-agnostic model client (monitor_spec.md §1, §5).

The API key comes only from ``APART_API_KEY``. It is never logged, stored in a
record, or written to ``repr``. Tests use ``FakeClient`` and make no calls.

Every real request requires a ``SpendGuard``: it charges a worst-case
reservation before the request is sent, refuses when the phase cap would be
exceeded, and settles against provider usage when available.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

try:  # POSIX only; the thread lock still protects a single process elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

PHASES = ("smoke", "gate", "pilot", "extension")


class ApiError(RuntimeError):
    """Raised after the configured API attempts are exhausted."""


class SpendConfigError(RuntimeError):
    """Spend configuration is missing or invalid; no request may be sent."""


class BudgetExceeded(RuntimeError):
    """The next request could exceed the phase cap; nothing was sent."""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    response_id: Optional[str]
    model: str
    snapshot: str
    provider_seed: Optional[int]


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens for one (provider, model slug)."""

    input_per_million: float
    output_per_million: float

    def __post_init__(self) -> None:
        for field_name in ("input_per_million", "output_per_million"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SpendConfigError(f"{field_name} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise SpendConfigError(f"{field_name} must be finite and > 0")


def _validated_cap(value: Any, phase: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpendConfigError(f"phase cap for {phase!r} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise SpendConfigError(f"phase cap for {phase!r} must be finite and > 0")
    return float(value)


@dataclass(frozen=True)
class SpendConfig:
    """Provider, model, phase caps and per-million-token prices.

    Provider, model slug and prices are **chosen by MM**; the repository must
    not ship a placeholder model. JSON shape (see ``SpendConfig.from_dict``)::

        {"provider": "<MM-approved provider>",
         "model": "<MM-approved model slug>",
         "phase_caps_usd": {"smoke": 0.10, "gate": 1.00,
                            "pilot": 8.90, "extension": 30.00},
         "prices": {"<provider>/<model slug>":
                    {"input_per_million": <number>,
                     "output_per_million": <number>}}}

    The ``$10`` reserve is deliberately outside this config; it is released
    only by MM.
    """

    provider: str
    model: str
    phase_caps_usd: Mapping[str, float]
    prices: Mapping[str, ModelPrice]

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise SpendConfigError("provider must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise SpendConfigError("model must be a non-empty string")
        if not isinstance(self.phase_caps_usd, Mapping) or not self.phase_caps_usd:
            raise SpendConfigError("phase_caps_usd must be a non-empty object")
        unknown = set(self.phase_caps_usd) - set(PHASES)
        if unknown:
            raise SpendConfigError(f"unknown phase(s): {sorted(unknown)}")
        for phase, cap in self.phase_caps_usd.items():
            _validated_cap(cap, phase)
        if not isinstance(self.prices, Mapping) or not self.prices:
            raise SpendConfigError("prices must be a non-empty object")
        for key, price in self.prices.items():
            if not isinstance(key, str) or "/" not in key:
                raise SpendConfigError(f"price key {key!r} must be 'provider/model'")
            if not isinstance(price, ModelPrice):
                raise SpendConfigError(f"price for {key!r} must be a ModelPrice")
        self.price_for(self.model)  # the configured model must be priced

    def price_for(self, model: str, provider: Optional[str] = None) -> ModelPrice:
        provider = provider or self.provider
        key = f"{provider}/{model}"
        price = self.prices.get(key)
        if price is None:
            raise SpendConfigError(f"no price configured for {key!r}")
        return price

    @classmethod
    def from_dict(cls, data: Any) -> "SpendConfig":
        if not isinstance(data, dict):
            raise SpendConfigError("spend config must be a JSON object")
        for required in ("provider", "model", "phase_caps_usd", "prices"):
            if required not in data:
                raise SpendConfigError(f"spend config missing {required!r}")
        raw_prices = data["prices"]
        if not isinstance(raw_prices, dict):
            raise SpendConfigError("prices must be an object")
        raw_caps = data["phase_caps_usd"]
        if not isinstance(raw_caps, dict):
            raise SpendConfigError("phase_caps_usd must be an object")
        prices: dict[str, ModelPrice] = {}
        for key, value in raw_prices.items():
            if not isinstance(value, dict):
                raise SpendConfigError(f"price for {key!r} must be an object")
            try:
                prices[key] = ModelPrice(
                    float(value["input_per_million"]),
                    float(value["output_per_million"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SpendConfigError(f"invalid price for {key!r}") from exc
        return cls(
            provider=data["provider"],
            model=data["model"],
            phase_caps_usd=dict(raw_caps),
            prices=prices,
        )

    @classmethod
    def from_file(cls, path: Path) -> "SpendConfig":
        path = Path(path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpendConfigError(f"cannot read spend config {path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SpendConfigError(f"invalid JSON in spend config {path}") from exc
        return cls.from_dict(data)


@dataclass(frozen=True)
class Reservation:
    """A charged worst-case amount for one request."""

    amount_usd: float
    phase: str
    run_id: str
    model: str


def _empty_ledger() -> dict:
    return {"version": 1, "phases": {}, "runs": {}}


def _ledger_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpendConfigError(f"ledger field {name!r} must be a number")
    if not math.isfinite(value) or value < 0:
        raise SpendConfigError(f"ledger field {name!r} must be finite and >= 0")
    return float(value)


def _ledger_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpendConfigError(f"ledger field {name!r} must be a non-negative integer")
    return value


class SpendGuard:
    """Thread- and process-safe phase budget with a persisted ledger.

    Every request is charged a worst-case reservation *before* it is sent.
    On success the reservation is settled against provider usage when that
    usage is present and well-formed; otherwise the reservation stands.
    """

    def __init__(
        self,
        config: SpendConfig,
        phase: str,
        ledger_path: Path,
        run_id: str,
    ) -> None:
        if not isinstance(config, SpendConfig):
            raise SpendConfigError("config must be a SpendConfig")
        if phase not in PHASES:
            raise SpendConfigError(f"phase must be one of {PHASES}")
        if phase not in config.phase_caps_usd:
            raise SpendConfigError(f"no phase cap configured for {phase!r}")
        if not isinstance(run_id, str) or not run_id.strip():
            raise SpendConfigError("run_id must be a non-empty string")
        self.config = config
        self.phase = phase
        self.run_id = run_id.strip()
        self.cap_usd = _validated_cap(config.phase_caps_usd[phase], phase)
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        self._mutate(lambda data: None)  # create and validate the ledger

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        lock_path = self.ledger_path.with_name(self.ledger_path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict:
        if not self.ledger_path.exists():
            return _empty_ledger()
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SpendConfigError(f"cannot read spend ledger {self.ledger_path}: {exc}") from exc
        except ValueError as exc:
            raise SpendConfigError(f"invalid JSON in spend ledger {self.ledger_path}") from exc
        if not isinstance(data, dict):
            raise SpendConfigError("spend ledger must be a JSON object")
        if not isinstance(data.get("phases"), dict) or not isinstance(data.get("runs"), dict):
            raise SpendConfigError("spend ledger is missing 'phases' or 'runs'")
        return data

    def _save(self, data: dict) -> None:
        tmp = self.ledger_path.with_name(
            f"{self.ledger_path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        tmp.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self.ledger_path)

    def _mutate(self, operation: Callable[[dict], Any]) -> Any:
        with self._thread_lock, self._file_lock():
            data = self._load()
            result = operation(data)
            self._save(data)
            return result


    def _phase_record(self, data: dict) -> dict:
        record = data["phases"].setdefault(self.phase, {"spent_usd": 0.0, "requests": 0})
        if not isinstance(record, dict):
            raise SpendConfigError(f"ledger phase {self.phase!r} is malformed")
        record["spent_usd"] = _ledger_number(
            record.get("spent_usd", 0.0), f"phases.{self.phase}.spent_usd"
        )
        record["requests"] = _ledger_count(
            record.get("requests", 0), f"phases.{self.phase}.requests"
        )
        return record

    def _run_record(self, data: dict) -> dict:
        record = data["runs"].setdefault(
            self.run_id, {"phase": self.phase, "spent_usd": 0.0, "requests": 0}
        )
        if not isinstance(record, dict):
            raise SpendConfigError(f"ledger run {self.run_id!r} is malformed")
        if record.get("phase") != self.phase:
            raise SpendConfigError(
                f"run_id {self.run_id!r} is already bound to phase {record.get('phase')!r}"
            )
        record["spent_usd"] = _ledger_number(
            record.get("spent_usd", 0.0), f"runs.{self.run_id}.spent_usd"
        )
        record["requests"] = _ledger_count(
            record.get("requests", 0), f"runs.{self.run_id}.requests"
        )
        return record

    def reserve(self, model: str, input_tokens: int, max_tokens: int) -> Reservation:
        """Charge a worst-case reservation before a request is sent."""
        if not isinstance(model, str) or not model.strip():
            raise SpendConfigError("model slug is required for a reservation")
        if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
            raise SpendConfigError("input_tokens must be a non-negative integer")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise SpendConfigError("max_tokens must be a positive integer")
        price = self.config.price_for(model)
        amount = (
            input_tokens * price.input_per_million / 1_000_000
            + max_tokens * price.output_per_million / 1_000_000
        )
        if not math.isfinite(amount) or amount <= 0:
            raise SpendConfigError("worst-case reservation must be positive")

        def operation(data: dict) -> Reservation:
            phase_rec = self._phase_record(data)
            run_rec = self._run_record(data)
            spent = phase_rec["spent_usd"]
            if spent + amount > self.cap_usd + 1e-12:
                raise BudgetExceeded(
                    f"phase {self.phase!r}: ${spent:.6f} spent + ${amount:.6f} "
                    f"reservation would exceed cap ${self.cap_usd:.6f}"
                )
            phase_rec["spent_usd"] = round(spent + amount, 12)
            phase_rec["requests"] += 1
            run_rec["spent_usd"] = round(run_rec["spent_usd"] + amount, 12)
            run_rec["requests"] += 1
            return Reservation(round(amount, 12), self.phase, self.run_id, model)

        return self._mutate(operation)


    def settle(
        self,
        reservation: Reservation,
        usage: Any,
        served_model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> float:
        """Adjust a reservation to provider usage, or keep it as worst case."""
        if not isinstance(reservation, Reservation):
            raise SpendConfigError("settle requires a Reservation")
        if reservation.phase != self.phase or reservation.run_id != self.run_id:
            raise SpendConfigError("reservation belongs to a different guard")
        tokens = _usage_tokens(usage)
        if tokens is None:
            return reservation.amount_usd  # worst case, never zero
        prompt_tokens, completion_tokens = tokens
        served = (
            served_model
            if isinstance(served_model, str) and served_model.strip()
            else reservation.model
        )
        provider_name = provider if isinstance(provider, str) and provider.strip() else None
        price = self.config.price_for(served, provider=provider_name)
        actual = (
            prompt_tokens * price.input_per_million / 1_000_000
            + completion_tokens * price.output_per_million / 1_000_000
        )
        if not math.isfinite(actual) or actual <= 0:
            return reservation.amount_usd
        delta = actual - reservation.amount_usd

        def operation(data: dict) -> float:
            phase_rec = self._phase_record(data)
            run_rec = self._run_record(data)
            phase_rec["spent_usd"] = round(max(0.0, phase_rec["spent_usd"] + delta), 12)
            run_rec["spent_usd"] = round(max(0.0, run_rec["spent_usd"] + delta), 12)
            return round(actual, 12)

        return self._mutate(operation)

    def totals(self) -> dict:
        def operation(data: dict) -> dict:
            phase_rec = self._phase_record(data)
            run_rec = self._run_record(data)
            return {
                "phase": self.phase,
                "cap_usd": self.cap_usd,
                "phase_spent_usd": phase_rec["spent_usd"],
                "phase_requests": phase_rec["requests"],
                "run_id": self.run_id,
                "run_spent_usd": run_rec["spent_usd"],
                "run_requests": run_rec["requests"],
            }

        return self._mutate(operation)


def _usage_tokens(usage: Any) -> Optional[tuple[int, int]]:
    """Accept OpenAI- or Anthropic-style usage; None if missing or malformed."""
    if not isinstance(usage, Mapping):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    for value in (prompt, completion):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
    return prompt, completion


def _input_token_bound(messages: Sequence[dict]) -> int:
    """Conservative token upper bound: UTF-8 bytes (each token covers >= 1 byte)."""
    total = 0
    for message in messages:
        try:
            total += len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            total += 1024
    return total + 64


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
        spend_guard: Optional[SpendGuard] = None,
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
        if spend_guard is not None:
            if not isinstance(spend_guard, SpendGuard):
                raise SpendConfigError("spend_guard must be a SpendGuard")
            if model is None:
                model = spend_guard.config.model
            elif model != spend_guard.config.model:
                raise SpendConfigError("model does not match the spend config model")
        self.spend_guard = spend_guard
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
        if self.spend_guard is None:
            raise SpendConfigError(
                "spend guard not configured; refusing to send a request"
            )
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise SpendConfigError("max_tokens must be a positive integer")
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = seed
        input_bound = _input_token_bound(payload["messages"])
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_attempts):
            # Charged before the request is sent; failure keeps it as worst case.
            reservation = self.spend_guard.reserve(self.model, input_bound, max_tokens)
            try:
                data = self._post_json(payload)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                continue
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                continue
            served_model = (
                data.get("model") if isinstance(data.get("model"), str) else self.model
            )
            provider = data.get("provider") if isinstance(data.get("provider"), str) else None
            self.spend_guard.settle(
                reservation,
                data.get("usage"),
                served_model=served_model,
                provider=provider,
            )
            return ModelResponse(
                text=text,
                response_id=data.get("id"),
                model=self.model,
                snapshot=self.snapshot,
                provider_seed=seed,
            )
        raise ApiError(f"model call failed after {self.max_attempts} attempts") from last_error

    def _post_json(self, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))


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
