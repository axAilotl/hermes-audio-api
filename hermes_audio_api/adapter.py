"""Gateway platform adapter that runs the audio HTTP listener alongside the other platforms."""

from __future__ import annotations

import logging
from typing import Any

from gateway.platforms.base import BasePlatformAdapter, SendResult

from . import PLATFORM_NAME
from .settings import Settings

logger = logging.getLogger(__name__)


class AudioAPIAdapter(BasePlatformAdapter):
    """Serves /v1/audio/* on its own port; it has no chats, so it never relays messages."""

    def __init__(self, config: Any, settings_loader) -> None:
        from gateway.config import Platform

        super().__init__(config, Platform(PLATFORM_NAME))
        self._settings_loader = settings_loader
        self._runner = None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        from aiohttp import web

        from .server import build_app

        settings: Settings = self._settings_loader()
        api_key = settings.api_key()
        if not api_key:
            self._set_fatal_error(
                "config_missing",
                "Set AUDIO_API_KEY (or API_SERVER_KEY) before enabling the audio API.",
                retryable=False,
            )
            return False
        runner = web.AppRunner(build_app(settings, api_key), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, settings.host, settings.port)
        try:
            await site.start()
        except OSError as exc:
            await runner.cleanup()
            self._set_fatal_error(
                "bind_failed", f"Cannot listen on {settings.host}:{settings.port}: {exc}",
                retryable=True,
            )
            return False
        self._runner = runner
        self._mark_connected()
        logger.info("Audio API listening on http://%s:%s/v1/audio", settings.host, settings.port)
        return True

    async def disconnect(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()
        self._mark_disconnected()

    async def send(self, chat_id: str, content: str, reply_to: str | None = None,
                   metadata: dict[str, Any] | None = None) -> SendResult:
        return SendResult(success=False, error="The audio API has no chats to send to.")

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": "Audio API", "type": "api"}
