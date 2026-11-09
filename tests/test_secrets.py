"""Bearer credentials stay within Hermes' active profile scope."""

from __future__ import annotations

import pytest
from agent import secret_scope

from hermes_audio_api.settings import Settings


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize(
    ("multiplex", "scope", "expected"),
    [
        (True, None, ""),
        (True, {}, ""),
        (True, {"AUDIO_API_KEY": " profile-key "}, "profile-key"),
        (False, None, "other-profile-key"),
        (False, {}, "other-profile-key"),
    ],
)
def test_bearer_key_respects_profile_scope(monkeypatch, tmp_path, reuse,
                                         multiplex, scope, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AUDIO_API_KEY", "other-profile-key")
    monkeypatch.setenv("API_SERVER_KEY", "other-server-key")
    previous = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope(scope)
    secret_scope.set_multiplex_active(multiplex)
    try:
        assert Settings(reuse_api_server_key=reuse).api_key() == expected
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous)


@pytest.mark.parametrize("reuse", [False, True])
def test_server_key_fallback_is_profile_scoped(monkeypatch, reuse):
    monkeypatch.setenv("AUDIO_API_KEY", "other-profile-key")
    monkeypatch.setenv("API_SERVER_KEY", "other-server-key")
    previous = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope({"API_SERVER_KEY": "profile-server-key"})
    secret_scope.set_multiplex_active(True)
    try:
        expected = "profile-server-key" if reuse else ""
        assert Settings(reuse_api_server_key=reuse).api_key() == expected
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous)
