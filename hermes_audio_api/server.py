"""aiohttp application serving OpenAI-compatible /v1/audio endpoints."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import shutil
import tempfile
from typing import Any

from aiohttp import web

from . import _core, audio
from .settings import Settings

logger = logging.getLogger(__name__)

MAX_JSON_BYTES = 1024 * 1024
MAX_FORM_FIELD_BYTES = 64 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
_MAX_ERROR_CHARS = 500
# Base filename handed to the TTS helper; providers pick their own codec, the extension only hints.
_TTS_BASE_SUFFIX = {
    "mp3": ".mp3", "ogg": ".ogg", "wav": ".wav", "flac": ".flac", "aac": ".mp3", "pcm": ".wav",
}
_EXPOSED_HEADERS = "X-Hermes-STT-Provider, X-Hermes-TTS-Provider, X-Hermes-Transcript-Filtered"

SETTINGS_KEY = web.AppKey("settings", Settings)
API_KEY_KEY = web.AppKey("api_key", str)
LIMITER_KEY = web.AppKey("limiter", asyncio.Semaphore)


class _BodyTooLarge(Exception):
    pass


def _error(message: str, status: int, *, err_type: str = "invalid_request_error",
           code: str | None = None, param: str | None = None) -> web.Response:
    payload = {"error": {"message": message[:_MAX_ERROR_CHARS], "type": err_type,
                         "param": param, "code": code}}
    return web.json_response(payload, status=status)


def _too_large(limit: int) -> web.Response:
    return _error(f"Request body exceeds {limit} bytes.", 413, code="body_too_large")


def _check_auth(request: web.Request) -> web.Response | None:
    expected = request.app[API_KEY_KEY]
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token and hmac.compare_digest(token.strip(), expected):
        return None
    return _error("Invalid or missing API key.", 401, err_type="authentication_error",
                  code="invalid_api_key")


def _cors_headers(request: web.Request) -> dict[str, str]:
    origins = request.app[SETTINGS_KEY].cors_origins
    origin = request.headers.get("Origin", "").rstrip("/")
    if not origin or not origins or ("*" not in origins and origin not in origins):
        return {}
    return {
        "Access-Control-Allow-Origin": "*" if "*" in origins else origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Expose-Headers": _EXPOSED_HEADERS,
        "Vary": "Origin",
    }


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    cors = _cors_headers(request)
    if request.method == "OPTIONS":
        return web.Response(status=204 if cors else 403, headers=cors)
    response = await handler(request)
    response.headers.update(cors)
    return response


async def _read_capped(read_chunk: Any, cap: int) -> bytes:
    """Drain ``read_chunk(size)``, raising _BodyTooLarge past ``cap`` bytes (chunked bodies too)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await read_chunk(STREAM_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > cap:
            raise _BodyTooLarge
        chunks.append(chunk)


def _remove_temp_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def _run_blocking(request: web.Request, func, *args, **kwargs):
    async with request.app[LIMITER_KEY]:
        return await asyncio.to_thread(func, *args, **kwargs)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _receive_form(
    reader: Any, work_dir: str, limit: int
) -> tuple[str | None, dict[str, str]] | web.Response:
    """Stream the multipart body to ``work_dir``, counting every byte against ``limit``."""
    fields: dict[str, str] = {}
    upload: str | None = None
    received = 0
    while (part := await reader.next()) is not None:
        if part.name == "file" and upload is None:
            upload = os.path.join(
                work_dir,
                "upload" + audio.upload_suffix(part.filename, part.headers.get("Content-Type")),
            )
            with open(upload, "wb") as handle:
                while chunk := await part.read_chunk(STREAM_CHUNK_BYTES):
                    received += len(chunk)
                    if received > limit:
                        return _too_large(limit)
                    handle.write(chunk)
        elif part.name:
            try:
                value = await _read_capped(part.read_chunk, MAX_FORM_FIELD_BYTES)
            except _BodyTooLarge:
                return _error(f"Form field '{part.name}' is too large.", 413, code="body_too_large")
            received += len(value)
            if received > limit:
                return _too_large(limit)
            fields[part.name] = value.decode(part.get_charset(default="utf-8"), "replace")
    return upload, fields


async def transcriptions(request: web.Request) -> web.StreamResponse:
    """POST /v1/audio/transcriptions: multipart upload -> configured Hermes STT provider."""
    if (denied := _check_auth(request)) is not None:
        return denied
    settings = request.app[SETTINGS_KEY]
    limit = settings.max_upload_bytes
    if request.content_length is not None and request.content_length > limit:
        return _too_large(limit)
    try:
        reader = await request.multipart()
    except Exception:
        return _error("Expected a multipart/form-data body.", 400)

    work_dir = tempfile.mkdtemp(prefix="hermes-audio-api-")
    try:
        try:
            received = await _receive_form(reader, work_dir, limit)
        except Exception as exc:  # aiohttp raises assorted errors on malformed multipart input
            logger.debug("audio_api rejected multipart body: %s", exc)
            return _error("Malformed multipart/form-data body.", 400)
        if isinstance(received, web.Response):
            return received
        upload, fields = received
        if upload is None:
            return _error("Missing 'file' field.", 400, param="file")
        if os.path.getsize(upload) == 0:
            return _error("Uploaded file is empty.", 400, param="file")

        response_format = (fields.get("response_format") or "json").strip().lower()
        if response_format not in audio.TRANSCRIPTION_FORMATS:
            return _error("Unsupported response_format; expected json, text or verbose_json.",
                          400, param="response_format")
        model = (fields.get("model") or "").strip() or None

        try:
            result = await _run_blocking(request, _core.transcribe, upload, model)
        except Exception as exc:  # the STT helper is documented not to raise; stay defensive
            logger.warning("audio_api transcription raised: %s", exc, exc_info=True)
            return _error("Transcription failed.", 500, err_type="server_error")
        if not result.get("success"):
            return _error(str(result.get("error") or "Transcription failed."), 400,
                          code="transcription_failed")

        transcript = str(result.get("transcript") or "")
        filtered = bool(result.get("filtered"))
        if transcript and _core.is_hallucination(transcript):
            transcript, filtered = "", True
        provider = str(result.get("provider") or "")
        headers = {}
        if provider:
            headers["X-Hermes-STT-Provider"] = provider
        if filtered:
            headers["X-Hermes-Transcript-Filtered"] = "true"

        if response_format == "text":
            return web.Response(text=transcript, content_type="text/plain", headers=headers)
        payload: dict[str, Any] = {"text": transcript}
        if response_format == "verbose_json":
            payload.update(task="transcribe", provider=provider or None, filtered=filtered)
        return web.json_response(payload, headers=headers)
    finally:
        _remove_temp_dir(work_dir)


def _speech_paths(result: dict[str, Any]) -> list[str]:
    paths = result.get("file_paths")
    if not isinstance(paths, list) or not paths:
        paths = [result.get("file_path")]
    return [str(path) for path in paths if path]


async def speech(request: web.Request) -> web.StreamResponse:
    """POST /v1/audio/speech: JSON text -> configured Hermes TTS provider -> audio stream."""
    if (denied := _check_auth(request)) is not None:
        return denied
    settings = request.app[SETTINGS_KEY]
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return _too_large(MAX_JSON_BYTES)
    try:
        body = json.loads(await _read_capped(request.content.read, MAX_JSON_BYTES))
    except _BodyTooLarge:
        return _too_large(MAX_JSON_BYTES)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("Request body must be JSON.", 400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)

    text = body.get("input")
    if not isinstance(text, str) or not text.strip():
        return _error("Missing or invalid 'input'.", 400, param="input")
    if len(text) > settings.max_input_chars:
        return _error(f"'input' exceeds {settings.max_input_chars} characters.", 400, param="input")
    response_format = str(body.get("response_format") or "mp3").strip().lower()
    if response_format == "ogg":
        response_format = "opus"
    if response_format not in audio.SPEECH_FORMATS:
        return _error("Unsupported response_format; expected one of: "
                      + ", ".join(audio.SPEECH_FORMATS), 400, param="response_format")
    container, media_type = audio.SPEECH_FORMATS[response_format]

    speed = body.get("speed")
    if speed is not None:
        if isinstance(speed, bool) or not isinstance(speed, int | float) or not 0.25 <= speed <= 4:
            return _error("'speed' must be a number between 0.25 and 4.0.", 400, param="speed")
        speed = float(speed)
    instructions = body.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        instructions = None
    voice = body.get("voice") if settings.pass_voice else None
    voice = voice.strip() if isinstance(voice, str) and voice.strip() else None

    work_dir = tempfile.mkdtemp(prefix="hermes-audio-api-")
    try:
        base = os.path.join(work_dir, "speech" + _TTS_BASE_SUFFIX[container])
        try:
            result = await _run_blocking(request, _core.synthesize, text.strip(), base,
                                         speed=speed, instructions=instructions, voice=voice)
        except Exception as exc:
            logger.warning("audio_api speech raised: %s", exc, exc_info=True)
            return _error("Speech generation failed.", 500, err_type="server_error")
        if not result.get("success"):
            return _error(str(result.get("error") or "Speech generation failed."), 400,
                          code="speech_failed")
        # Only the request directory is removed afterwards; a provider that ignores output_path
        # leaves its file in its own cache, as it would for any other TTS call.
        paths = _speech_paths(result)
        if not paths or not all(os.path.isfile(path) for path in paths):
            return _error("Speech output is missing.", 500, err_type="server_error")

        output = paths[0]
        if len(paths) > 1 or audio.sniff_container(output) != container:
            if audio.ffmpeg_path() is None:
                produced = audio.sniff_container(output) or "unknown"
                return _error(
                    f"The TTS provider produced {produced}"
                    + (f" in {len(paths)} parts" if len(paths) > 1 else "")
                    + f", not {response_format}; install ffmpeg to convert it.",
                    400, param="response_format", code="format_unavailable",
                )
            output = os.path.join(work_dir, f"converted.{container}")
            try:
                await _run_blocking(request, audio.convert, paths, output, container)
            except Exception as exc:
                logger.warning("audio_api conversion failed: %s", exc)
                return _error("Could not convert speech to the requested format.", 500,
                              err_type="server_error")

        headers = {"Content-Type": media_type, "Content-Length": str(os.path.getsize(output))}
        provider = str(result.get("provider") or "")
        if provider:
            headers["X-Hermes-TTS-Provider"] = provider
        return await _stream_file(request, output, headers)
    finally:
        _remove_temp_dir(work_dir)


async def _stream_file(
    request: web.Request, path: str, headers: dict[str, str]
) -> web.StreamResponse:
    response = web.StreamResponse(status=200, headers={**headers, **_cors_headers(request)})
    await response.prepare(request)
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(STREAM_CHUNK_BYTES):
                await response.write(chunk)
        await response.write_eof()
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.info("audio_api client disconnected while streaming speech")
    return response


def build_app(settings: Settings, api_key: str) -> web.Application:
    if not api_key:
        raise ValueError("an API key is required")
    app = web.Application(middlewares=[_cors_middleware],
                          client_max_size=max(settings.max_upload_bytes, MAX_JSON_BYTES))
    app[SETTINGS_KEY] = settings
    app[API_KEY_KEY] = api_key
    app[LIMITER_KEY] = asyncio.Semaphore(settings.max_concurrency)
    app.router.add_get("/health", health)
    app.router.add_post("/v1/audio/transcriptions", transcriptions)
    app.router.add_post("/v1/audio/speech", speech)
    return app
