from __future__ import annotations

import pytest
from conftest import make_tone, needs_ffmpeg, run_with_client

from hermes_audio_api import audio
from hermes_audio_api.settings import Settings


@pytest.mark.parametrize(
    ("header", "container"),
    [
        (b"OggS\0\0\0\0\0\0\0\0", "ogg"),
        (b"RIFF\0\0\0\0WAVEfmt ", "wav"),
        (b"fLaC\0\0\0\0\0\0\0\0", "flac"),
        (b"ID3\x04\0\0\0\0\0\0\0\0", "mp3"),
        (b"\xff\xfb\x90\x00\0\0\0\0\0\0\0\0", "mp3"),
        (b"\xff\xf1\x50\x80\0\0\0\0\0\0\0\0", "aac"),
        (b"\0\0\0\x20ftypM4A ", "mp4"),
        (b"not audio at all", None),
    ],
)
def test_sniff_container(tmp_path, header, container) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(header)
    assert audio.sniff_container(path) == container


@needs_ffmpeg
@pytest.mark.parametrize("fmt", ["mp3", "wav", "ogg", "flac", "adts"])
def test_sniff_matches_real_ffmpeg_output(tmp_path, fmt) -> None:
    expected = {"adts": "aac"}.get(fmt, fmt)
    assert audio.sniff_container(make_tone(tmp_path / f"tone.{fmt}", fmt)) == expected


@pytest.mark.parametrize(
    ("filename", "content_type", "suffix"),
    [
        ("clip.M4A", None, ".m4a"),
        ("../../etc/passwd", None, ".webm"),
        ("clip." + "x" * 40, "audio/wav", ".wav"),
        (None, "audio/ogg; codecs=opus", ".ogg"),
        (None, None, ".webm"),
    ],
)
def test_upload_suffix_is_short_and_safe(filename, content_type, suffix) -> None:
    assert audio.upload_suffix(filename, content_type) == suffix


def test_cors_only_for_listed_origins() -> None:
    settings = Settings(cors_origins=("https://chat.example",))

    async def scenario(client):
        allowed = await client.options(
            "/v1/audio/speech", headers={"Origin": "https://chat.example"}
        )
        denied = await client.options("/v1/audio/speech", headers={"Origin": "https://evil.example"})
        health = await client.get("/health", headers={"Origin": "https://chat.example"})
        return allowed.status, allowed.headers, denied.status, health.headers

    allowed_status, allowed, denied_status, health = run_with_client(settings, scenario)
    assert allowed_status == 204
    assert allowed["Access-Control-Allow-Origin"] == "https://chat.example"
    assert "X-Hermes-TTS-Provider" in allowed["Access-Control-Expose-Headers"]
    assert denied_status == 403
    assert health["Access-Control-Allow-Origin"] == "https://chat.example"
