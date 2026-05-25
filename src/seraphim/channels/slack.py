"""Slack channel — Conversations API polling."""

from __future__ import annotations

import asyncio
import logging

import httpx

from seraphim.channels.base import (
    BaseChannel,
    ChannelHandler,
    ChannelMessage,
    ChannelRegistry,
    ChannelStatus,
)

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api/{method}"


@ChannelRegistry.register("slack")
class SlackChannel(BaseChannel):
    name = "slack"

    def __init__(self) -> None:
        super().__init__()
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._watermark: str = ""

    async def start(self, handler: ChannelHandler) -> None:
        from seraphim.settings import settings
        cfg = settings.channels.slack
        token = cfg.bot_token.get_secret_value()
        if not token:
            raise ValueError("Slack bot token not configured (channels.slack.bot_token)")
        self._status = ChannelStatus.RUNNING
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        self._task = asyncio.create_task(self._poll(cfg.channel_id, handler))

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    async def send(self, chat_id: str, text: str) -> None:
        if not self._client:
            return
        url = _SLACK_API.format(method="chat.postMessage")
        try:
            await self._client.post(url, json={"channel": chat_id, "text": text})
        except Exception as exc:
            logger.warning("Slack send failed: %s", exc)

    async def _poll(self, channel_id: str, handler: ChannelHandler) -> None:
        url = _SLACK_API.format(method="conversations.history")
        while self._status == ChannelStatus.RUNNING:
            try:
                params: dict = {"channel": channel_id, "limit": 10}
                if self._watermark:
                    params["oldest"] = self._watermark
                resp = await self._client.get(url, params=params)
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue
                for m in reversed(data.get("messages", [])):
                    ts: str = m.get("ts", "")
                    if ts <= self._watermark:
                        continue
                    self._watermark = ts
                    text = (m.get("text") or "").strip()
                    if not text or m.get("bot_id"):
                        continue
                    msg = ChannelMessage(
                        channel="slack",
                        user_id=m.get("user", "unknown"),
                        chat_id=channel_id,
                        text=text,
                        raw=m,
                    )
                    reply = await handler(msg)
                    await self.send(channel_id, reply)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Slack poll error: %s", exc)
            await asyncio.sleep(3)
