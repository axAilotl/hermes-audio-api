from __future__ import annotations

import os

import aiohttp
from conftest import AUTH, run_with_client

from hermes_audio_api.settings import Settings

URL = "/v1/audio/transcriptions"


def _form(data: bytes = b"\x1aE\xdf\xa3 fake webm", filename: str = "clip.webm", **fields):
    form = aiohttp.FormData()
    form.add_field("file", data, filename=filename, content_type="audio/webm")
    for name, value in fields.items():
        form.add_field(name, value)
    return form


def test_requires_bearer_key(fake_core) -> None:
    async def scenario(client):
        missing = await client.post(URL, data=_form())
        wrong = await client.post(URL, data=_form(), headers={"Authorization": "Bearer nope"})
        non_ascii = await client.post(
            URL, data=_form(), headers={"Authorization": "Bearer clé".encode().decode("latin-1")}
        )
        return missing.status, wrong.status, non_ascii.status

    assert run_with_client(Settings(), scenario) == (401, 401, 401)
    assert fake_core.transcribe_calls == []


def test_json_text_and_verbose_formats(fake_core) -> None:
    async def scenario(client):
        as_json = await (await client.post(URL, data=_form(model="whisper-1"), headers=AUTH)).json()
        text = await client.post(URL, data=_form(response_format="text"), headers=AUTH)
        verbose = await client.post(URL, data=_form(response_format="verbose_json"), headers=AUTH)
        return as_json, text.headers, await text.text(), await verbose.json()

    as_json, text_headers, text, verbose = run_with_client(Settings(), scenario)
    assert as_json == {"text": "hello world"}
    assert text == "hello world"
    assert text_headers["X-Hermes-STT-Provider"] == "fake-stt"
    assert verbose["task"] == "transcribe" and verbose["provider"] == "fake-stt"
    assert fake_core.transcribe_calls[0][1] == "whisper-1"
    assert fake_core.transcribe_seen_bytes[0] == b"\x1aE\xdf\xa3 fake webm"


def test_upload_is_removed_after_the_request(fake_core) -> None:
    async def scenario(client):
        return (await client.post(URL, data=_form(), headers=AUTH)).status

    assert run_with_client(Settings(), scenario) == 200
    upload_path = fake_core.transcribe_calls[0][0]
    assert upload_path.endswith(".webm")
    assert not os.path.exists(os.path.dirname(upload_path))


def test_hallucinated_transcript_is_blanked(fake_core) -> None:
    fake_core.transcript = "Thank you."

    async def scenario(client):
        response = await client.post(URL, data=_form(), headers=AUTH)
        return await response.json(), response.headers.get("X-Hermes-Transcript-Filtered")

    assert run_with_client(Settings(), scenario) == ({"text": ""}, "true")


def test_rejects_unsupported_format_without_transcribing(fake_core) -> None:
    async def scenario(client):
        return (await client.post(URL, data=_form(response_format="srt"), headers=AUTH)).status

    assert run_with_client(Settings(), scenario) == 400
    assert fake_core.transcribe_calls == []


def test_chunked_upload_over_limit_is_refused(fake_core) -> None:
    settings = Settings(max_upload_bytes=1024)
    boundary = "hermesboundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + b"\0" * 4096 + f"\r\n--{boundary}--\r\n".encode()

    async def chunks():
        for start in range(0, len(body), 512):
            yield body[start:start + 512]

    async def scenario(client):
        response = await client.post(
            URL, data=chunks(),
            headers={**AUTH, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return response.status, response.request_info.headers.get("Content-Length")

    status, sent_length = run_with_client(settings, scenario)
    assert sent_length is None  # chunked: only the streaming counter can catch it
    assert status == 413
    assert fake_core.transcribe_calls == []


def test_declared_length_over_limit_is_refused_early(fake_core) -> None:
    settings = Settings(max_upload_bytes=1024)

    async def scenario(client):
        return (await client.post(URL, data=_form(b"\0" * 4096), headers=AUTH)).status

    assert run_with_client(settings, scenario) == 413


def test_provider_failure_is_reported(fake_core) -> None:
    fake_core.transcribe_result = {"success": False, "error": "no STT provider configured"}

    async def scenario(client):
        response = await client.post(URL, data=_form(), headers=AUTH)
        return response.status, await response.json()

    status, body = run_with_client(Settings(), scenario)
    assert status == 400
    assert body["error"]["message"] == "no STT provider configured"


def test_missing_file_field(fake_core) -> None:
    form = aiohttp.FormData()
    form.add_field("model", "whisper-1")

    async def scenario(client):
        return (await client.post(URL, data=form, headers=AUTH)).status

    assert run_with_client(Settings(), scenario) == 400


def test_malformed_multipart_is_a_client_error(fake_core) -> None:
    async def scenario(client):
        response = await client.post(
            URL, data=b"--wrong\r\nnot a part\r\n",
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=expected"},
        )
        return response.status

    assert run_with_client(Settings(), scenario) == 400
    assert fake_core.transcribe_calls == []
