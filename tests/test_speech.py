from __future__ import annotations

import os

from conftest import AUTH, make_tone, needs_ffmpeg, run_with_client

from hermes_audio_api import audio
from hermes_audio_api.settings import Settings

URL = "/v1/audio/speech"


def _speak(client, **body):
    return client.post(URL, json={"input": "hello there", **body}, headers=AUTH)


def test_requires_bearer_key(fake_core) -> None:
    async def scenario(client):
        return (await client.post(URL, json={"input": "hi"})).status

    assert run_with_client(Settings(), scenario) == 401
    assert fake_core.synthesize_calls == []


def test_matching_format_streams_provider_file(fake_core) -> None:
    async def scenario(client):
        response = await _speak(client)
        return response.status, response.headers, await response.read()

    status, headers, data = run_with_client(Settings(), scenario)
    assert status == 200
    assert headers["Content-Type"] == "audio/mpeg"
    assert headers["X-Hermes-TTS-Provider"] == "fake-tts"
    assert data == fake_core.speech_files[0][1]
    assert not os.path.exists(os.path.dirname(fake_core.synthesize_calls[0]["output_path"]))


def test_unconvertible_format_is_an_error_not_a_mislabel(fake_core, monkeypatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_path", lambda: None)

    async def scenario(client):
        response = await _speak(client, response_format="wav")
        return response.status, await response.json()

    status, body = run_with_client(Settings(), scenario)
    assert status == 400
    assert body["error"]["code"] == "format_unavailable"
    assert "mp3" in body["error"]["message"]


@needs_ffmpeg
def test_mismatched_format_is_converted(fake_core, tmp_path) -> None:
    fake_core.speech_files = [(".mp3", make_tone(tmp_path / "tone.mp3", "mp3").read_bytes())]

    async def scenario(client):
        wav = await _speak(client, response_format="wav")
        opus = await _speak(client, response_format="opus")
        pcm = await _speak(client, response_format="pcm")
        return [(r.status, r.headers["Content-Type"], await r.read()) for r in (wav, opus, pcm)]

    (wav_status, wav_type, wav), (_, opus_type, opus), (_, pcm_type, pcm) = run_with_client(
        Settings(), scenario
    )
    assert wav_status == 200 and wav_type == "audio/wav" and wav[8:12] == b"WAVE"
    assert opus_type == "audio/ogg" and opus.startswith(b"OggS")
    assert pcm_type == "audio/pcm" and pcm and not pcm.startswith((b"RIFF", b"ID3"))


@needs_ffmpeg
def test_multi_part_output_is_joined(fake_core, tmp_path) -> None:
    one = make_tone(tmp_path / "a.mp3", "mp3", 0.3).read_bytes()
    two = make_tone(tmp_path / "b.mp3", "mp3", 0.3).read_bytes()
    fake_core.speech_files = [(".mp3", one), (".mp3", two)]

    async def scenario(client):
        response = await _speak(client, response_format="mp3")
        return response.status, await response.read()

    status, data = run_with_client(Settings(), scenario)
    assert status == 200
    assert data not in (one, two) and len(data) > len(one)


def test_input_validation(fake_core) -> None:
    async def scenario(client):
        too_long = await client.post(URL, json={"input": "x" * 11}, headers=AUTH)
        empty = await client.post(URL, json={"input": "  "}, headers=AUTH)
        bad_speed = await _speak(client, speed=9)
        bad_format = await _speak(client, response_format="mid")
        not_json = await client.post(URL, data=b"{", headers=AUTH)
        return [r.status for r in (too_long, empty, bad_speed, bad_format, not_json)]

    assert run_with_client(Settings(max_input_chars=10), scenario) == [400] * 5
    assert fake_core.synthesize_calls == []


def test_voice_is_ignored_unless_enabled(fake_core) -> None:
    async def scenario(client):
        await _speak(client, voice="alloy", speed=1.5, instructions="calm")

    run_with_client(Settings(), scenario)
    run_with_client(Settings(pass_voice=True), scenario)
    first, second = fake_core.synthesize_calls
    assert first["voice"] is None and second["voice"] == "alloy"
    assert first["speed"] == 1.5 and first["instructions"] == "calm"


def test_provider_failure_is_reported(fake_core) -> None:
    fake_core.synthesize_result = {"success": False, "error": "TTS provider unavailable"}

    async def scenario(client):
        response = await _speak(client)
        return response.status, (await response.json())["error"]["message"]

    assert run_with_client(Settings(), scenario) == (400, "TTS provider unavailable")
