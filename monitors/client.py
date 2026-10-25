"""Provider-agnostic model client (monitor_spec.md §1, §5).

The API key comes only from ``APART_API_KEY``. It is never logged, stored in a
record, or written to ``repr``. Tests use ``FakeClient`` and make no calls.

Every real request requires a ``LedgerGuard`` over the canonical ledger
(``monitors.ledger``): each attempt is reserved durably before it is sent.
``SpendGuard`` below is the legacy v1 guard; it can no longer authorize a
request and is kept only for reading historical v1 ledgers.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from .ledger import (
    LedgerError,
    LedgerStore,
    Policy,
    mark_dispatched,
    mark_unknown,
    reserve as ledger_reserve,
    settle_event,
)

try:  # POSIX only; the thread lock still protects a single process elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

PHASES = ("smoke", "gate", "pilot", "extension")

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_apart_env(path: Optional[Path] = None) -> int:
    """Load only APART_* keys from a .env file when not already set.

    Never overrides an existing environment variable, never prints values, and
    ignores every non-APART key. Returns the number of variables loaded.
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    loaded = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("APART_") or len(key) <= len("APART_"):
            continue
        if not all(char.isalnum() or char == "_" for char in key):
            continue
        if key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
        loaded += 1
    return loaded


def _ssl_context() -> ssl.SSLContext:
    """Build a verifying TLS context with a usable CA bundle.

    This Python installation has no default cafile, so fall back to
    ``APART_CA_BUNDLE``, then ``certifi`` if installed, then the macOS system
    bundle. Verification is never disabled.
    """
    bundle = os.environ.get("APART_CA_BUNDLE")
    if not bundle:
        try:
            import certifi

            bundle = certifi.where()
        except ImportError:
            bundle = None
    if not bundle:
        system_bundle = Path("/etc/ssl/cert.pem")
        bundle = str(system_bundle) if system_bundle.is_file() else None
    context = ssl.create_default_context(cafile=bundle)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


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
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    # Provider-reported usage exactly as returned, or None when the provider
    # omitted it or returned it malformed. ``None`` means UNKNOWN, never zero
    # cost; nothing is invented and no default is substituted.
    usage: Optional[Mapping[str, Any]] = None


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
        try:
            price = self.config.price_for(served, provider=provider_name)
        except SpendConfigError:
            if provider_name is None:
                raise
            # OpenRouter reports the upstream provider; billing keys on the
            # configured gateway unless an upstream-specific price is listed.
            price = self.config.price_for(served)
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


def read_v1_ledger(path: Path) -> dict:
    """Read a historical v1 SpendGuard ledger without creating or changing anything."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SpendConfigError(f"cannot read v1 ledger {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise SpendConfigError(f"{path} is not a v1 spend ledger")
    if not isinstance(data.get("phases"), dict) or not isinstance(data.get("runs"), dict):
        raise SpendConfigError(f"{path} is missing 'phases' or 'runs'")
    for run_id, run in data["runs"].items():
        if not isinstance(run, dict) or run.get("phase") not in PHASES:
            raise SpendConfigError(f"run {run_id!r} in {path} is malformed")
        _ledger_number(run.get("spent_usd"), f"runs.{run_id}.spent_usd")
        _ledger_count(run.get("requests"), f"runs.{run_id}.requests")
    return data


class LedgerGuard:
    """Authorizes real requests against the canonical ledger (T3 S2).

    Built for one opened run. Every HTTP attempt is reserved durably before it
    is sent, marked dispatched, and then settled or marked unknown. Any ledger
    or persistence error propagates: the caller must stop, and nothing more is
    sent. The model comes only from the bound spend config.
    """

    def __init__(self, store: LedgerStore, policy: Policy, config_hash: str, run_id: str) -> None:
        ledger = store.load(expected_config_hash=config_hash, policy=policy)
        header = ledger["runs"].get(run_id)
        if header is None:
            raise LedgerError(f"run {run_id!r} is not open in the ledger")
        if header["config_sha256"] != config_hash:
            raise LedgerError("run was opened under a different config")
        self.provider, self.model = policy.require_model()
        provenance = header.get("provenance") or {}
        if provenance.get("model") != f"{self.provider}/{self.model}":
            raise LedgerError("run provenance does not name the bound model")
        self.store = store
        self.policy = policy
        self.config_hash = config_hash
        self.run_id = run_id
        self.base_url = provenance.get("base_url")

    def check_endpoint(self, base_url: str) -> None:
        if base_url != self.base_url:
            raise LedgerError("endpoint differs from the one recorded in the run provenance")

    def reserve(self, call_id: str, attempt: int, input_tokens: int, max_tokens: int) -> dict:
        amount = self.policy.worst_case_micro(input_tokens, max_tokens)
        return ledger_reserve(self.store, self.policy, self.config_hash, self.run_id,
                              call_id, attempt, f"{self.provider}/{self.model}", amount)

    def dispatched(self, event_id: str) -> None:
        mark_dispatched(self.store, self.config_hash, event_id)

    def settle(self, event_id: str, usage: Any, served_model: Optional[str],
               served_provider: Optional[str]) -> str:
        """Settle from provider usage and return the state actually recorded.

        A charge above the reservation is recorded as ``overrun`` (with a budget
        incident) and returned as ``"overrun"``, so it is never reported to the
        caller as an ordinary successful settlement.
        """
        cost = self.policy.cost_micro(usage, served_model, served_provider)
        if cost is None:
            mark_unknown(self.store, self.config_hash, event_id, "usage_missing_or_model_unpriced")
            return "unknown"
        return settle_event(self.store, self.config_hash, event_id, cost)["state"]

    def unknown(self, event_id: str, reason: str) -> None:
        mark_unknown(self.store, self.config_hash, event_id, reason)


def validate_base_url(url: str) -> str:
    """https only, except plain http to localhost for local stubs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        pass
    elif parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        pass
    else:
        raise SpendConfigError(f"base URL must be https (got {url!r})")
    if parsed.username or parsed.password or parsed.query:
        raise SpendConfigError("base URL must not contain credentials or a query")
    return url.rstrip("/")


class ModelClient:
    """Minimal OpenAI-compatible chat client, authorized only by a ``LedgerGuard``.

    The endpoint comes from ``base_url`` or ``APART_BASE_URL`` (validated); the
    model comes only from the guard's bound config (``APART_MODEL`` is ignored).
    Retries: exponential backoff, ``max_attempts`` HTTP attempts, then ``ApiError``.
    """

    def __init__(
        self,
        *,
        guard: Optional[LedgerGuard] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        snapshot: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_attempts: int = 4,
        backoff_base_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        provider_order: Optional[Sequence[str]] = None,
        load_env: bool = True,
    ) -> None:
        if isinstance(guard, SpendGuard):
            raise SpendConfigError("the legacy SpendGuard cannot authorize requests; use LedgerGuard")
        if guard is not None and not isinstance(guard, LedgerGuard):
            raise SpendConfigError("guard must be a LedgerGuard")
        if load_env:
            load_apart_env()
        key = api_key if api_key is not None else os.environ.get("APART_API_KEY")
        if not key:
            raise RuntimeError(
                "APART_API_KEY is not set; keys are read from the environment only"
            )
        self._api_key = key
        self.base_url = validate_base_url(
            base_url or os.environ.get("APART_BASE_URL") or "https://api.openai.com/v1"
        )
        self.guard = guard
        if guard is not None:
            if model is not None and model != guard.model:
                raise SpendConfigError("model does not match the bound spend config")
            guard.check_endpoint(self.base_url)
            model = guard.model
        self.model = model or "unconfigured"
        self.provider_order = list(provider_order or [])
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
        call_id: Optional[str] = None,
    ) -> ModelResponse:
        if self.guard is None:
            raise SpendConfigError("no LedgerGuard configured; refusing to send a request")
        if not isinstance(call_id, str) or not call_id.strip():
            raise SpendConfigError("call_id is required for a guarded request")
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
        if self.provider_order:
            payload["provider"] = {"order": list(self.provider_order)}
        input_bound = _input_token_bound(payload["messages"])
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_attempts):
            # Durable reservation and dispatch marker first; any ledger error
            # propagates here and nothing is sent.
            event = self.guard.reserve(call_id, attempt, input_bound, max_tokens)
            self.guard.dispatched(event["event_id"])
            try:
                data = self._post_json(payload)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_error = ApiError(f"HTTP {exc.code}: {detail}")
                self.guard.unknown(event["event_id"], f"http_error_{exc.code}")
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = exc
                self.guard.unknown(event["event_id"], f"transport_error_{type(exc).__name__}")
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                continue
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                last_error = exc
                self.guard.unknown(event["event_id"], "malformed_response")
                if attempt + 1 < self.max_attempts:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                continue
            # What the provider reported, or None. The requested model is never
            # substituted: pricing falls back to the bound model inside
            # ``Policy.cost_micro`` on its own, but provenance must show "unknown".
            served_model = (
                data.get("model")
                if isinstance(data.get("model"), str) and data.get("model").strip()
                else None
            )
            provider = data.get("provider") if isinstance(data.get("provider"), str) else None
            raw_usage = data.get("usage")
            usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else None
            self.guard.settle(event["event_id"], raw_usage, served_model, provider)
            return ModelResponse(
                text=text,
                response_id=data.get("id"),
                model=self.model,
                snapshot=self.snapshot,
                provider_seed=seed,
                served_model=served_model,
                served_provider=provider,
                usage=usage,
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
        with urllib.request.urlopen(
            request, timeout=self.timeout_s, context=_ssl_context()
        ) as response:
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
        provider_order: Optional[Sequence[str]] = None,
    ) -> None:
        self._responder = responder or (
            lambda messages, temperature, seed, index: dry_run_response(messages)
        )
        self.model = model
        self.snapshot = snapshot
        self.provider_order = list(provider_order or [])
        self.calls: list[dict] = []

    def call_model(
        self,
        messages: Sequence[dict],
        temperature: float,
        seed: Optional[int] = None,
        max_tokens: int = 250,
        call_id: Optional[str] = None,
    ) -> ModelResponse:
        index = len(self.calls)
        self.calls.append(
            {
                "messages": list(messages),
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_tokens,
                "call_id": call_id,
            }
        )
        text = self._responder(messages, temperature, seed, index)
        return ModelResponse(
            text=text,
            response_id=f"fake-{index + 1}",
            model=self.model,
            snapshot=self.snapshot,
            provider_seed=seed,
            served_model=self.model,
            served_provider=None,
        )


class ScriptedClient(FakeClient):
    """FakeClient returning a fixed sequence of replies (tests)."""

    def __init__(self, texts: Sequence[str], **kwargs) -> None:
        replies = iter(texts)
        super().__init__(responder=lambda *_: next(replies), **kwargs)
