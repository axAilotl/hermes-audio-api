from __future__ import annotations

import pytest

from hermes_audio_api import _core, register
from hermes_audio_api.settings import Settings


class FakeContext:
    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.platforms: list[dict] = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_platform(self, **kwargs) -> None:
        self.platforms.append(kwargs)


@pytest.fixture
def secrets(monkeypatch) -> dict:
    values: dict[str, str] = {}
    monkeypatch.setattr(_core, "get_secret", lambda name: values.get(name, ""))
    return values


def test_registers_one_platform_enabled_only_with_a_key(secrets) -> None:
    context = FakeContext()
    register(context)

    (entry,) = context.platforms
    assert entry["name"] == "audio_api"
    assert entry["check_fn"]() is True
    assert entry["is_connected"](None) is False
    secrets["API_SERVER_KEY"] = "server-key"
    assert entry["is_connected"](None) is True


def test_dedicated_key_wins_and_reuse_can_be_disabled(secrets) -> None:
    secrets.update(API_SERVER_KEY="server-key", AUDIO_API_KEY="audio-key")
    assert Settings().api_key() == "audio-key"
    del secrets["AUDIO_API_KEY"]
    assert Settings(reuse_api_server_key=False).api_key() == ""


def test_settings_are_clamped() -> None:
    settings = Settings.load(
        {"port": "99999", "max_upload_mb": 0, "max_concurrency": True,
         "cors_origins": "https://a.example/, https://b.example"}.get
    )
    assert settings.port == 65535
    assert settings.max_upload_bytes == 1024 * 1024
    assert settings.max_concurrency == 2
    assert settings.cors_origins == ("https://a.example", "https://b.example")
