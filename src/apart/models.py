from typing import Literal, Optional, Protocol
from dataclasses import dataclass

# class ModelClient(Protocol):
#     def generate(
#             self, 
#             messages: list[dict[str, str]],
#             *,
#             temperature: float = 0.0,
#     ) -> str: 

@dataclass
class AgentAction:
    tool_name: str
    tool_input: str

@dataclass
class AgentMessage:
    sender_id: str
    withheld: bool
    message_content: str
    message_type: Literal["withheld", "deliver", "placebo"]
    receiver_id: Optional[str] = None


@dataclass
class TrialMetadata:
    trial_id: str
    condition: str
    seed: int


@dataclass
class TrialResult:
    metadata: TrialMetadata
    actions: dict = None  # agent_id -> AgentAction
    messages: list = None  # list[AgentMessage]
    outcome: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.actions is None:
            self.actions = {}
        if self.messages is None:
            self.messages = []