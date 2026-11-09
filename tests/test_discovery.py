"""Real Hermes path: manifest scan, namespaced import, gateway enablement, adapter lifecycle."""

from __future__ import annotations

import asyncio
import shutil
import socket
from pathlib import Path

import aiohttp
import pytest
import yaml

PLUGIN = "hermes-audio-api"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    def make(port: int, *, enabled: bool = True) -> Path:
        home = tmp_path / "hermes"
        plugins_dir = home / "plugins"
        plugins_dir.mkdir(parents=True)
        shutil.copytree(
            Path(__file__).resolve().parents[1],
            plugins_dir / PLUGIN,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache",
                                          ".ruff_cache", "dist", "build"),
        )
        config = {"plugins": {"enabled": [PLUGIN] if enabled else [],
                              "entries": {PLUGIN: {"settings": {"port": port}}}}}
        (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        return home

    return make


def _load(manager):
    from gateway.platform_registry import platform_registry

    manager.discover_and_load()
    loaded = manager._plugins[PLUGIN]
    assert loaded.enabled and loaded.error is None, loaded.error
    return platform_registry.get("audio_api")


def test_gateway_enables_platform_only_with_a_key(hermes_home, monkeypatch) -> None:
    from gateway.config import Platform, load_gateway_config
    from hermes_cli.plugins import PluginManager

    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    hermes_home(_free_port())
    manager = PluginManager()
    try:
        entry = _load(manager)
        assert entry is not None
        without_key = load_gateway_config().platforms.get(Platform("audio_api"))
        assert without_key is None or not without_key.enabled
        monkeypatch.setenv("AUDIO_API_KEY", "audio-key")
        assert load_gateway_config().platforms[Platform("audio_api")].enabled is True
    finally:
        manager.unload()


def test_adapter_serves_and_releases_its_port(hermes_home, monkeypatch) -> None:
    from gateway.config import PlatformConfig
    from hermes_cli.plugins import PluginManager

    monkeypatch.setenv("AUDIO_API_KEY", "audio-key")
    port = _free_port()
    hermes_home(port)
    manager = PluginManager()
    try:
        adapter = _load(manager).adapter_factory(PlatformConfig(enabled=True))

        async def scenario():
            assert await adapter.connect() is True
            try:
                async with aiohttp.ClientSession() as session:
                    base = f"http://127.0.0.1:{port}"
                    async with session.get(f"{base}/health") as response:
                        health = response.status
                    async with session.post(f"{base}/v1/audio/speech", json={"input": "hi"}) as r:
                        unauthenticated = r.status
                    async with session.post(
                        f"{base}/v1/audio/speech", json={"input": "hi", "response_format": "mid"},
                        headers={"Authorization": "Bearer audio-key"},
                    ) as r:
                        authenticated_validation = r.status
                sent = await adapter.send("chat", "hello")
            finally:
                await adapter.disconnect()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
            return health, unauthenticated, authenticated_validation, sent.success

        assert asyncio.run(scenario()) == (200, 401, 400, False)
    finally:
        manager.unload()


def test_adapter_refuses_to_start_without_a_key(hermes_home, monkeypatch) -> None:
    from gateway.config import PlatformConfig
    from hermes_cli.plugins import PluginManager

    monkeypatch.delenv("AUDIO_API_KEY", raising=False)
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    hermes_home(_free_port())
    manager = PluginManager()
    try:
        adapter = _load(manager).adapter_factory(PlatformConfig(enabled=True))
        assert asyncio.run(adapter.connect()) is False
    finally:
        manager.unload()


def test_unscoped_multiplex_does_not_enable_or_start_listener(hermes_home, monkeypatch):
    from agent import secret_scope
    from gateway.config import Platform, PlatformConfig, load_gateway_config
    from hermes_cli.plugins import PluginManager

    monkeypatch.setenv("AUDIO_API_KEY", "other-profile-key")
    monkeypatch.setenv("API_SERVER_KEY", "other-server-key")
    port = _free_port()
    hermes_home(port)
    previous = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope(None)
    secret_scope.set_multiplex_active(True)
    manager = PluginManager()
    try:
        entry = _load(manager)
        configured = load_gateway_config().platforms.get(Platform("audio_api"))
        assert configured is None or not configured.enabled
        adapter = entry.adapter_factory(PlatformConfig(enabled=True))
        assert asyncio.run(adapter.connect()) is False
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    finally:
        manager.unload()
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous)
