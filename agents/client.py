"""Fake model client for the agents package (extension_spec.md §8, §17).

No network, no API key. Records every call's requested parameters so the
per-call logging contract (§8) can be exercised without a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass
class AgentModelResponse:
    text: str
    response_id: str
    model: str
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    # Provider-reported usage, or None when unavailable. ``None`` means UNKNOWN;
    # it is never replaced by an invented or zero value.
    usage: Optional[dict] = None


class FakeAgentClient:
    """In-memory client; the responder maps (messages, temperature, seed, index) -> text.

    The ``call_model`` signature mirrors ``monitors.client.ModelClient`` exactly
    (including ``call_id``), so the agent stage code is identical whether it is
    driven by this fake or by a real guarded client. ``call_id`` is recorded so
    tests can assert per-stage call identity.
    """

    def __init__(
        self,
        responder: Optional[Callable[[Sequence[dict], float, Optional[int], int], str]] = None,
        model: str = "fake-agent",
        snapshot: str = "fake-snapshot",
        usage: Optional[dict] = None,
    ) -> None:
        self._responder = responder or (lambda *_: '{"plan": "A", "note": ""}')
        self.model = model
        self.snapshot = snapshot
        self.usage = usage
        self.calls: list[dict] = []

    def call_model(
        self,
        messages: Sequence[dict],
        temperature: float = 1.0,
        seed: Optional[int] = None,
        max_tokens: int = 150,
        call_id: Optional[str] = None,
    ) -> AgentModelResponse:
        index = len(self.calls)
        self.calls.append(
            {
                "call_id": call_id,
                "messages": [dict(m) for m in messages],
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_tokens,
            }
        )
        text = self._responder(messages, temperature, seed, index)
        return AgentModelResponse(
            text=text,
            response_id=f"fake-{index + 1}",
            model=self.model,
            served_model=self.model,
            served_provider="fake",
            usage=dict(self.usage) if self.usage is not None else None,
        )
