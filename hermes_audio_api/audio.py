"""Pure audio helpers: container sniffing, upload suffixes, and ffmpeg conversion."""

from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
from pathlib import Path

# OpenAI ``response_format`` values for /v1/audio/speech -> (container id, media type).
SPEECH_FORMATS: dict[str, tuple[str, str]] = {
    "mp3": ("mp3", "audio/mpeg"),
    "opus": ("ogg", "audio/ogg"),
    "aac": ("aac", "audio/aac"),
    "flac": ("flac", "audio/flac"),
    "wav": ("wav", "audio/wav"),
    "pcm": ("pcm", "audio/pcm"),
}
TRANSCRIPTION_FORMATS = frozenset({"json", "text", "verbose_json"})

_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "mp4": "audio/mp4",
}
# ffmpeg output arguments per container; pcm matches OpenAI's raw 24 kHz mono s16le.
_FFMPEG_OUTPUT_ARGS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-f", "mp3"],
    "ogg": ["-c:a", "libopus", "-f", "ogg"],
    "aac": ["-c:a", "aac", "-f", "adts"],
    "flac": ["-c:a", "flac", "-f", "flac"],
    "wav": ["-c:a", "pcm_s16le", "-f", "wav"],
    "pcm": ["-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", "-f", "s16le"],
}
# Upload media types -> suffix; the platform mimetypes table often lacks audio types.
_UPLOAD_SUFFIXES = {
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/opus": ".ogg",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
    "audio/aac": ".aac", "audio/flac": ".flac", "audio/x-flac": ".flac",
}
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,8}$")
_FFMPEG_TIMEOUT_SECONDS = 120


def sniff_container(path: str | Path) -> str | None:
    """Return the container id of an audio file from its magic bytes, or None."""
    with open(path, "rb") as handle:
        header = handle.read(12)
    if header.startswith(b"OggS"):
        return "ogg"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    if header.startswith(b"fLaC"):
        return "flac"
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "mp4"
    if header.startswith(b"ID3"):
        return "mp3"
    if len(header) >= 2 and header[0] == 0xFF:
        # ADTS AAC sets layer bits to 00; MPEG audio (mp3) uses a non-zero layer.
        if (header[1] & 0xF6) == 0xF0:
            return "aac"
        if (header[1] & 0xE0) == 0xE0:
            return "mp3"
    return None


def media_type_for(container: str | None) -> str:
    return _MEDIA_TYPES.get(container or "", "application/octet-stream")


def upload_suffix(filename: str | None, content_type: str | None) -> str:
    """Pick a short, safe temp-file suffix so the STT helper can detect the format."""
    if filename:
        suffix = Path(filename).suffix.lower()
        if _SAFE_SUFFIX.match(suffix):
            return suffix
    if content_type:
        base_type = content_type.split(";", 1)[0].strip().lower()
        if base_type in _UPLOAD_SUFFIXES:
            return _UPLOAD_SUFFIXES[base_type]
        guessed = mimetypes.guess_extension(base_type, strict=False)
        if guessed and _SAFE_SUFFIX.match(guessed):
            return guessed
    return ".webm"


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def convert(inputs: list[str], output: str, container: str) -> None:
    """Concatenate ``inputs`` and encode them into ``container`` at ``output`` with ffmpeg."""
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not installed")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for source in inputs:
        command += ["-i", source]
    if len(inputs) > 1:
        streams = "".join(f"[{index}:a]" for index in range(len(inputs)))
        graph = f"{streams}concat=n={len(inputs)}:v=0:a=1[out]"
        command += ["-filter_complex", graph, "-map", "[out]"]
    else:
        command += ["-vn"]
    command += _FFMPEG_OUTPUT_ARGS[container] + [output]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"ffmpeg failed: {detail[0][:200]}")
