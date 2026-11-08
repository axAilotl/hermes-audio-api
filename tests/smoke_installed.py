"""Run after installing the built wheel, from outside the source import path."""

import importlib
import os
import tempfile
from pathlib import Path

import yaml
from gateway.platform_registry import platform_registry
from hermes_cli.plugins import PluginManager

PLUGIN = "hermes-audio-api"
module = importlib.import_module("hermes_audio_api")
assert not Path(module.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])
with tempfile.TemporaryDirectory() as temporary:
    os.environ["HERMES_HOME"] = temporary
    os.environ["AUDIO_API_KEY"] = "smoke-test-key"
    (Path(temporary) / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": [PLUGIN]}}), encoding="utf-8"
    )
    manager = PluginManager()
    try:
        manager.discover_and_load()
        loaded = manager._plugins[PLUGIN]
        assert loaded.enabled and not loaded.error, loaded.error
        entry = platform_registry.get("audio_api")
        assert entry is not None and entry.is_connected(None)
        print(f"Installed wheel discovered and registered: {PLUGIN}")
    finally:
        manager.unload()
