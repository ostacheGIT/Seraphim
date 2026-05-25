"""Telegram channel — httpx long-polling."""

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

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_MAX_LEN = 4096


def _api(token: str, method: str) -> str:
    return _API_BASE.format(token=token, method=method)


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, max(len(text), 1), size)]


@ChannelRegistry.register("telegram")
class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self, handler: ChannelHandler) -> None:
        from seraphim.settings import settings
        cfg = settings.channels.telegram
        token = cfg.token.get_secret_value()
        if not token:
            raise ValueError("Telegram token not configured (channels.telegram.token)")
        self._status = ChannelStatus.RUNNING
        self._client = httpx.AsyncClient(timeout=60)
        allowed = set(cfg.allowed_chat_ids)
        self._task = asyncio.create_task(self._poll(token, allowed, handler))

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    async def send(self, chat_id: str, text: str) -> None:
        from seraphim.settings import settings
        token = settings.channels.telegram.token.get_secret_value()
        if not token or not self._client:
            return
        url = _api(token, "sendMessage")
        for chunk in _chunks(text or " ", _MAX_LEN):
            try:
                await self._client.post(url, json={"chat_id": chat_id, "text": chunk})
            except Exception as exc:
                logger.warning("Telegram send failed: %s", exc)

    async def _poll(self, token: str, allowed: set[int], handler: ChannelHandler) -> None:
        offset = 0
        url = _api(token, "getUpdates")
        while self._status == ChannelStatus.RUNNING:
            try:
                resp = await self._client.get(
                    url,
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                )
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg_data = update.get("message", {})
                    text = (msg_data.get("text") or "").strip()
                    if not text:
                        continue
                    chat_id = str(msg_data["chat"]["id"])
                    user_id = str(msg_data.get("from", {}).get("id", chat_id))
                    if allowed and int(chat_id) not in allowed:
                        logger.debug("Telegram: blocked chat_id=%s", chat_id)
                        continue
                    msg = ChannelMessage(
                        channel="telegram",
                        user_id=user_id,
                        chat_id=chat_id,
                        text=text,
                        raw=msg_data,
                    )
                    reply = await handler(msg)
                    await self.send(chat_id, reply)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Telegram poll error: %s", exc)
                await asyncio.sleep(5)
