"""
Connector Contract (F29).
Shared data models for request/response.
"""
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class CommandRequest:
    platform: str  # "telegram" | "discord"
    sender_id: str
    channel_id: str
    username: str
    message_id: str
    text: str
    timestamp: float

@dataclass
class CommandResponse:
    text: str
    files: List[str] = field(default_factory=list)  # Local paths to upload
    buttons: List[dict] = field(default_factory=list) # Simple quick replies
