"""Plugin settings resolved from ``plugins.entries.hermes-audio-api.settings``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import _core

AUDIO_API_KEY_ENV = "AUDIO_API_KEY"
API_SERVER_KEY_ENV = "API_SERVER_KEY"

ConfigGetter = Callable[..., Any]


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _origins(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip().rstrip("/") for item in value if str(item).strip())


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8643
    max_upload_bytes: int = 25 * 1024 * 1024
    max_input_chars: int = 4096
    max_concurrency: int = 2
    cors_origins: tuple[str, ...] = ()
    pass_voice: bool = False
    reuse_api_server_key: bool = True

    @classmethod
    def load(cls, get_config: ConfigGetter) -> Settings:
        defaults = cls()
        return cls(
            host=str(get_config("host", defaults.host) or defaults.host).strip(),
            port=_int(get_config("port", defaults.port), defaults.port, 1, 65535),
            max_upload_bytes=_int(get_config("max_upload_mb", 25), 25, 1, 1024) * 1024 * 1024,
            max_input_chars=_int(
                get_config("max_input_chars", defaults.max_input_chars),
                defaults.max_input_chars, 1, 200_000,
            ),
            max_concurrency=_int(
                get_config("max_concurrency", defaults.max_concurrency),
                defaults.max_concurrency, 1, 32,
            ),
            cors_origins=_origins(get_config("cors_origins", [])),
            pass_voice=bool(get_config("pass_voice", False)),
            reuse_api_server_key=bool(get_config("reuse_api_server_key", True)),
        )

    def api_key(self) -> str:
        """The bearer key clients must send; empty means the server must not start."""
        key = _core.get_secret(AUDIO_API_KEY_ENV)
        if not key and self.reuse_api_server_key:
            key = _core.get_secret(API_SERVER_KEY_ENV)
        return key
