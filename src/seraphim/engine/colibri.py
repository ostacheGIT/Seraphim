"""Moteur colibri — gros modèles MoE servis en local (https://github.com/JustVugg/colibri).

colibri expose une API compatible OpenAI (`coli serve`, par défaut
http://127.0.0.1:8000/v1). Ce moteur :
  - découvre le modèle chargé (`GET /v1/models`) ;
  - démarre colibri à la demande (`coli start`) s'il ne répond pas, pour ne pas
    occuper la RAM / la carte graphique quand on ne s'en sert pas ;
  - transmet les appels d'outils (format OpenAI) aux agents de Seraphim ;
  - ignore le raisonnement (`reasoning_content`) dans la réponse streamée.

L'API serveur de colibri est texte seulement : la vision de l'écran reste sur Ollama.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from seraphim.engine.base import ChatMessage, ChatResult

logger = logging.getLogger(__name__)


class ColibriUnavailable(RuntimeError):
    pass


class ColibriEngine:
    id = "colibri"
    name = "colibri"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        home: str = "",
        model: str = "",
        api_key: str = "",
        autostart: bool = True,
        start_timeout: float = 240.0,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.home = Path(home) if home else None
        self.model = model                     # vide = celui que colibri a chargé
        self.api_key = api_key
        self.autostart = autostart
        self.start_timeout = start_timeout
        self.timeout = timeout
        self._start_lock = asyncio.Lock()

    # ── Disponibilité ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def installed(self) -> bool:
        return bool(self.home and (self.home / "c" / "coli").exists())

    async def is_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.base_url}/v1/models", headers=self._headers())
                if r.status_code != 200:
                    return False
                if not self.model:
                    data = r.json().get("data") or []
                    if data:
                        self.model = data[0].get("id", "") or ""
                return True
        except Exception:
            return False

    def _coli(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        """Lance `coli …` (scripts Python de colibri, bibliothèque standard seulement)."""
        assert self.home is not None
        return subprocess.run(
            [sys.executable, str(self.home / "c" / "coli"), *args],
            cwd=str(self.home), capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    async def ensure_running(self) -> None:
        if await self.is_up():
            return
        if not (self.autostart and self.installed()):
            raise ColibriUnavailable(
                f"colibri ne répond pas sur {self.base_url}. Lance-le avec `coli start` "
                f"dans {self.home or 'le dossier de colibri'}.")
        async with self._start_lock:
            if await self.is_up():
                return
            logger.info("Starting colibri (%s)…", self.home)
            await self._free_ollama()
            # `coli start` seul reste au premier plan : --background rend la main
            r = await asyncio.to_thread(self._coli, "start", "--background", "--no-browser")
            if r.returncode != 0:
                err = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[-400:]
                raise ColibriUnavailable(f"colibri n'a pas pu démarrer : {err}")
            deadline = time.monotonic() + self.start_timeout
            while time.monotonic() < deadline:
                if await self.is_up():
                    logger.info("colibri ready (%s)", self.model)
                    return
                await asyncio.sleep(2)
            raise ColibriUnavailable(
                f"colibri ne répond toujours pas après {self.start_timeout:.0f} s "
                "(`coli logs -n 50` dans son dossier pour voir pourquoi).")

    @staticmethod
    async def _free_ollama() -> None:
        """Décharge les modèles Ollama (ils se rechargent seuls au besoin) pour que
        colibri dispose de la carte graphique et de la RAM au démarrage."""
        try:
            from seraphim.settings import settings
            base = settings.engine.base_url.rstrip("/")
            async with httpx.AsyncClient(base_url=base, timeout=10) as c:
                loaded = (await c.get("/api/ps")).json().get("models") or []
                for m in loaded:
                    await c.post("/api/generate", json={"model": m.get("name"), "keep_alive": 0})
        except Exception as exc:
            logger.debug("could not unload Ollama models: %s", exc)

    async def stop(self) -> None:
        if self.installed():
            await asyncio.to_thread(self._coli, "stop")

    # ── Appels ───────────────────────────────────────────────────────────────

    def _payload(self, messages: list[ChatMessage], **extra: Any) -> dict[str, Any]:
        msgs = []
        for m in messages:
            cm: dict[str, Any] = {"role": m.get("role", "user"), "content": m.get("content") or ""}
            for k in ("name", "tool_call_id", "tool_calls"):
                if m.get(k):
                    cm[k] = m[k]
            msgs.append(cm)
        payload: dict[str, Any] = {"model": self.model or "default", "messages": msgs}
        payload.update({k: v for k, v in extra.items() if v is not None})
        return payload

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        await self.ensure_running()
        payload = self._payload(messages, tools=tools or None,
                                temperature=kwargs.get("temperature"),
                                max_tokens=kwargs.get("max_tokens"))
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10)) as c:
            r = await c.post(f"{self.base_url}/v1/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        out: ChatMessage = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        return {"messages": [out], "usage": data.get("usage")}

    async def stream_chat_api(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        await self.ensure_running()
        payload = self._payload(messages, stream=True, temperature=kwargs.get("temperature"))
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10)) as c, \
                c.stream("POST", f"{self.base_url}/v1/chat/completions",
                         headers=self._headers(), json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    delta = json.loads(data_str)["choices"][0].get("delta") or {}
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                content = delta.get("content")      # reasoning_content ignoré
                if content:
                    yield content
