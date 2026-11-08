"""Hermes registration entry point for the OpenAI-compatible audio API plugin."""

from __future__ import annotations

from .settings import Settings

PLATFORM_NAME = "audio_api"


def _aiohttp_available() -> bool:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return True


def register(ctx) -> None:
    """Register the audio listener as a gateway platform; it starts once a key is configured."""

    def load_settings() -> Settings:
        return Settings.load(ctx.get_config)

    def is_connected(_config) -> bool:
        return bool(load_settings().api_key())

    def adapter_factory(config):
        from .adapter import AudioAPIAdapter

        return AudioAPIAdapter(config, load_settings)

    ctx.register_platform(
        name=PLATFORM_NAME,
        label="Audio API",
        adapter_factory=adapter_factory,
        check_fn=_aiohttp_available,
        is_connected=is_connected,
        install_hint="pip install aiohttp",
        emoji="🎙️",
    )


__all__ = ["register"]
