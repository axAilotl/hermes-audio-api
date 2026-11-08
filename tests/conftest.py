from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_audio_api import _core
from hermes_audio_api.server import build_app
from hermes_audio_api.settings import Settings

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def run_with_client(settings: Settings, scenario):
    """Run ``scenario(client)`` against a live test server for ``settings``."""

    async def main():
        client = TestClient(TestServer(build_app(settings, KEY)))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()

    return asyncio.run(main())


def make_tone(path: Path, fmt: str, seconds: float = 0.3) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-f", fmt, str(path)],
        check=True,
    )
    return path


class FakeCore:
    """Stand-in for the Hermes STT/TTS entry points, recording what the server asked for."""

    def __init__(self) -> None:
        self.transcribe_calls: list[tuple[str, str | None]] = []
        self.transcribe_seen_bytes: list[bytes] = []
        self.transcript = "hello world"
        self.transcribe_result: dict | None = None
        self.synthesize_calls: list[dict] = []
        self.speech_files: list[tuple[str, bytes]] = [(".mp3", b"ID3" + b"\0" * 64)]
        self.synthesize_result: dict | None = None

    def transcribe(self, path: str, model: str | None) -> dict:
        self.transcribe_calls.append((path, model))
        self.transcribe_seen_bytes.append(Path(path).read_bytes())
        if self.transcribe_result is not None:
            return self.transcribe_result
        return {"success": True, "transcript": self.transcript, "provider": "fake-stt"}

    def synthesize(self, text: str, output_path: str, **kwargs) -> dict:
        self.synthesize_calls.append({"text": text, "output_path": output_path, **kwargs})
        if self.synthesize_result is not None:
            return self.synthesize_result
        base = Path(output_path)
        written = []
        for index, (suffix, data) in enumerate(self.speech_files):
            target = base.with_name(f"{base.stem}_{index}{suffix}")
            target.write_bytes(data)
            written.append(str(target))
        return {"success": True, "file_path": written[0], "file_paths": written,
                "provider": "fake-tts"}


@pytest.fixture
def fake_core(monkeypatch) -> FakeCore:
    fake = FakeCore()
    monkeypatch.setattr(_core, "transcribe", fake.transcribe)
    monkeypatch.setattr(_core, "synthesize", fake.synthesize)
    monkeypatch.setattr(_core, "is_hallucination", lambda text: text.strip() == "Thank you.")
    return fake
