# Hermes Audio API

OpenAI-compatible `/v1/audio/transcriptions` and `/v1/audio/speech` endpoints
backed by whatever speech-to-text and text-to-speech providers your Hermes
profile already uses. A standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent)
plugin.

Point any OpenAI-style client (Open WebUI, a voice front end, `openai` SDK
scripts) at it and it gets Hermes' configured STT/TTS without its own provider
keys.

## Requirements

- Hermes **0.23.0 or newer**, running the messaging gateway (`hermes gateway`).
- Working `stt` and `tts` configuration in the profile (`hermes tools` →
  Voice). The plugin adds endpoints; it does not add providers.
- `ffmpeg` on `PATH` to return speech in a format other than the one your TTS
  provider produces, or to join multi-part output. Without it, a mismatched
  format is refused with `format_unavailable` rather than mislabelled.

## Install

```sh
hermes plugins install axAilotl/hermes-audio-api --enable
```

Accept Hermes' dependency prompt (`aiohttp`). Then set a key and restart the
gateway:

```sh
hermes config set AUDIO_API_KEY "$(openssl rand -hex 24)"   # stored in the profile's .env
```

The Desktop's plugin settings page has the same key as a masked field.

If `AUDIO_API_KEY` is unset the plugin accepts `API_SERVER_KEY`, so clients
that already talk to the Hermes API server can reuse it. Turn that off with
`reuse_api_server_key: false`. With no key at all the listener does not start.

## Configure

Optional settings under `plugins.entries.hermes-audio-api.settings`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `host` | `127.0.0.1` | Listen address. Expose it only behind a firewall or reverse proxy. |
| `port` | `8643` | Listen port (the API server uses 8642). |
| `max_upload_mb` | `25` | Largest transcription upload, enforced while streaming. |
| `max_input_chars` | `4096` | Longest speech `input`. |
| `max_concurrency` | `2` | STT/TTS jobs run at once. |
| `cors_origins` | `[]` | Browser origins allowed, or `["*"]`. |
| `pass_voice` | `false` | Forward `voice` to the TTS provider as its voice ID. |

```yaml
plugins:
  enabled:
    - hermes-audio-api
  entries:
    hermes-audio-api:
      settings:
        port: 8643
        cors_origins: ["http://localhost:3000"]
```

## Endpoints

All `/v1/audio/*` requests need `Authorization: Bearer <key>`. `GET /health`
is unauthenticated.

### `POST /v1/audio/transcriptions`

Multipart form with `file` and optional `model` and `response_format`
(`json`, `text` or `verbose_json`). `model` is passed to the configured STT
provider. Whisper-style hallucinations on silence ("Thank you.") are blanked
and flagged with `X-Hermes-Transcript-Filtered: true`.

```sh
curl http://127.0.0.1:8643/v1/audio/transcriptions \
  -H "Authorization: Bearer $AUDIO_API_KEY" \
  -F file=@clip.webm -F response_format=verbose_json
```

### `POST /v1/audio/speech`

JSON with `input` and optional `response_format` (`mp3`, `opus`, `aac`,
`flac`, `wav`, `pcm`; default `mp3`), `speed` (0.25–4.0) and `instructions`.
`model` is ignored; the profile's TTS provider decides. `voice` is ignored
unless `pass_voice` is on, because OpenAI voice names mean nothing to most
other providers. `Content-Type` always describes the bytes actually returned;
`pcm` is raw 24 kHz mono 16-bit little-endian, as OpenAI returns it.

```sh
curl http://127.0.0.1:8643/v1/audio/speech \
  -H "Authorization: Bearer $AUDIO_API_KEY" -H "Content-Type: application/json" \
  -d '{"input": "Hello from Hermes", "response_format": "mp3"}' -o hello.mp3
```

Responses carry `X-Hermes-STT-Provider` / `X-Hermes-TTS-Provider`. Errors use
the OpenAI error shape.

## How it runs

The plugin registers a gateway platform called `audio_api`. The gateway starts
it like any other platform once a key is configured, and it serves its own
port; it never sends or receives chat messages. It is a separate listener
rather than routes on the API server because Hermes has no plugin surface for
adding API-server routes. Its routes are audio-only, so the larger upload
limit cannot leak onto chat endpoints.

Uploads and generated audio live in a per-request temporary directory that is
removed when the response finishes, including on client disconnect. A TTS
provider that ignores the requested output path may leave its file in its own
cache, as it would for any other Hermes TTS call.

Under `gateway.multiplex_profiles` each profile that enables the plugin binds
its own configured port, so give them different ports. The listener requires
an active profile secret scope containing `AUDIO_API_KEY` (or `API_SERVER_KEY`
when reuse is enabled). Missing scopes and scoped keys fail closed, even if
the process environment contains another profile's key. Tests cover credential
isolation and refusal to start an unscoped listener; concurrent listeners for
multiple profiles are not covered yet.

## Hermes internals used

Hermes has no public "transcribe with the configured provider" or "speak with
the configured provider" call, so the plugin uses the same entry points the CLI
and gateway use: `tools.transcription_tools.transcribe_audio`,
`tools.tts_tool.text_to_speech_tool` and `is_whisper_hallucination`. They are
imported in one module, `hermes_audio_api/_core.py`, and `hermes plugins
validate` reports them as private imports. A Hermes release that moves them
needs a one-file update here.

## Testing and development

CI tests Hermes v0.23.0 on Linux, macOS and Windows, plus Hermes main on Linux.
It covers auth, streaming upload limits (chunked bodies included), every
response format, real ffmpeg conversion and joining, format sniffing, CORS,
real plugin discovery and gateway enablement, and the adapter binding and
releasing its port.

```sh
git clone --branch v0.23.0 --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-agent
python -m pip install --no-deps -e ../hermes-agent
python -m pip install -r tests/requirements-host.txt '.[dev]' build
python -m pytest -q
ruff check .
hermes plugins doctor . --ci
hermes plugins validate .
```

## License

MIT
