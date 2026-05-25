"""Channel abstraction — BaseChannel, ChannelMessage, ChannelRegistry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable


@dataclass
class ChannelMessage:
    channel: str
    user_id: str
    chat_id: str
    text: str
    raw: dict = field(default_factory=dict)


class ChannelStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR   = "error"


ChannelHandler = Callable[[ChannelMessage], Awaitable[str]]


class BaseChannel(ABC):
    name: str = ""

    def __init__(self) -> None:
        self._status = ChannelStatus.STOPPED

    @abstractmethod
    async def start(self, handler: ChannelHandler) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, chat_id: str, text: str) -> None: ...

    def status(self) -> ChannelStatus:
        return self._status


class ChannelRegistry:
    _channels: dict[str, type[BaseChannel]] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(channel_cls: type[BaseChannel]) -> type[BaseChannel]:
            cls._channels[name] = channel_cls
            return channel_cls
        return decorator

    @classmethod
    def get(cls, name: str) -> type[BaseChannel]:
        if name not in cls._channels:
            raise KeyError(f"Channel '{name}' not registered")
        return cls._channels[name]

    @classmethod
    def list_names(cls) -> list[str]:
        return list(cls._channels.keys())

    @classmethod
    def get_enabled(cls) -> list[str]:
        from seraphim.settings import settings
        ch = settings.channels
        enabled: list[str] = []
        if ch.telegram.enabled and "telegram" in cls._channels:
            enabled.append("telegram")
        if ch.slack.enabled and "slack" in cls._channels:
            enabled.append("slack")
        if ch.webhook.enabled and "webhook" in cls._channels:
            enabled.append("webhook")
        return enabled


__all__ = ["ChannelMessage", "ChannelStatus", "ChannelHandler", "BaseChannel", "ChannelRegistry"]
