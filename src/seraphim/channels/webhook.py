"""Webhook channel — FastAPI router for POST /channels/webhook."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from seraphim.channels.base import ChannelMessage

logger = logging.getLogger(__name__)


class _WebhookPayload(BaseModel):
    text: str
    user_id: str = "webhook"
    chat_id: str = "webhook"


def get_fastapi_router() -> APIRouter:
    router = APIRouter(tags=["channels"])

    @router.post("/channels/webhook")
    async def webhook_endpoint(
        payload: _WebhookPayload,
        x_seraphim_secret: Optional[str] = Header(None),
    ):
        from seraphim.settings import settings
        expected = settings.channels.webhook.secret.get_secret_value()
        if expected and x_seraphim_secret != expected:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

        from seraphim.channels.handler import handle_channel_message
        msg = ChannelMessage(
            channel="webhook",
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            text=payload.text,
        )
        reply = await handle_channel_message(msg)
        return {"reply": reply}

    return router
