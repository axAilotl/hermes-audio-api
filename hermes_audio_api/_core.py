"""The Hermes internals this plugin calls, imported lazily and in one place.

Hermes exposes no public "transcribe with the configured STT provider" or
"speak with the configured TTS provider" function, so the plugin calls the same
tool-level entry points the CLI and gateway use. Keeping every such import here
makes the coupling visible and keeps a host-side move to a single fix.
"""

from __future__ import annotations

import json
from typing import Any


def transcribe(file_path: str, model: str | None) -> dict[str, Any]:
    from tools.transcription_tools import transcribe_audio

    return transcribe_audio(file_path, model=model, source="audio_api")


def is_hallucination(transcript: str) -> bool:
    try:
        from tools.voice_mode_transcript import is_whisper_hallucination
    except ImportError:  # Hermes releases before the voice-mode split
        from tools.voice_mode import is_whisper_hallucination
    return bool(is_whisper_hallucination(transcript))


def synthesize(
    text: str,
    output_path: str,
    *,
    speed: float | None = None,
    instructions: str | None = None,
    voice: str | None = None,
) -> dict[str, Any]:
    from tools.tts_tool import text_to_speech_tool

    kwargs: dict[str, Any] = {"text": text, "output_path": output_path}
    if speed is not None:
        kwargs["speed"] = speed
    if instructions:
        kwargs["instructions"] = instructions
    if voice:
        kwargs["voice_id"] = voice
    raw = text_to_speech_tool(**kwargs)
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "error": "TTS returned an unreadable result"}
    if not isinstance(result, dict):
        return {"success": False, "error": "TTS returned no result"}
    return result


def get_secret(name: str) -> str:
    """Fail-closed profile-scoped secret read (the gateway adapter pattern)."""
    from agent.secret_scope import UnscopedSecretError
    from agent.secret_scope import get_secret as scoped_get_secret

    try:
        value = scoped_get_secret(name, None)
    except UnscopedSecretError:
        # Ambient credentials may belong to a different multiplexed profile.
        return ""
    return (value or "").strip()
