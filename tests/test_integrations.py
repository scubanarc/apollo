from unittest.mock import Mock

import pytest
import requests

from apollo import aitools, estools, navidrome, ratings
from apollo.runtime import scope
from apollo.settings import Settings


@pytest.mark.parametrize("score,expected", [(-20, 1), (0, 1), (50, 3), (80, 4), (150, 5)])
def test_navidrome_rating_mapping(score, expected):
    assert navidrome.apollo_rating_to_navidrome(score) == expected


def test_sync_unchanged_and_updates(config, monkeypatch):
    cursor = Mock()
    cursor.fetchall.return_value = [
        {"artist": "A", "title": "B", "calculated_rating": 80},
        {"artist": "C", "title": "D", "calculated_rating": 100},
    ]
    monkeypatch.setattr(ratings, "get_db_connection", lambda: (Mock(), cursor))
    monkeypatch.setattr(navidrome, "_subsonic_get", lambda *a, **k: {})
    monkeypatch.setattr(estools, "get_es", lambda: (Mock(), "test"))
    monkeypatch.setattr(
        navidrome, "find_song_by_artist_title", lambda a, t: {"id": a, "userRating": 4}
    )
    update = Mock()
    monkeypatch.setattr(navidrome, "set_rating_by_song_id", update)
    with scope(config):
        result = navidrome.update_all_ratings()
    assert result["updated"] == 1 and result["unchanged"] == 1 and result["failed"] == 0
    update.assert_called_once_with("C", 5)


def test_navidrome_http_errors_do_not_expose_tokens(config, monkeypatch):
    config = Settings(
        dict(
            config.values,
            NAVIDROME_URL="https://example.invalid",
            NAVIDROME_UN="name",
            NAVIDROME_PWD="password",
        ),
        config.path,
    )
    session = Mock()
    session.get.side_effect = requests.ConnectionError(
        "https://example.invalid/?t=private-token&p=secret"
    )
    monkeypatch.setattr(navidrome.requests, "Session", lambda: session)
    with scope(config), pytest.raises(RuntimeError) as error:
        navidrome._subsonic_get("ping")
    assert "private-token" not in str(error.value) and "secret" not in str(error.value)
    session.close.assert_called_once()


def test_ai_provider_client_closed(config, monkeypatch):
    config = Settings(
        dict(
            config.values,
            OPENAI_API_KEY="test",
            OPENAI_BASE_URL="https://example.invalid",
            OPENAI_MODEL="test",
        ),
        config.path,
    )
    client = Mock()
    client.chat.completions.create.return_value.choices = [Mock(message=Mock(content="A - B"))]
    monkeypatch.setattr(aitools, "OpenAI", lambda **kwargs: client)
    with scope(config):
        assert aitools.get_playlist("synthwave", 1) == "A - B"
    client.close.assert_called_once()


def test_codex_provider_uses_stdin_and_timeout(config, monkeypatch):
    config = Settings(
        dict(config.values, AI_PROVIDER="codex", CODEX_TIMEOUT_SECONDS=30), config.path
    )
    monkeypatch.setattr(aitools, "_resolve_codex_bin", lambda: "/bin/codex")
    process = Mock(return_value=Mock(returncode=0, stdout="A - B", stderr=""))
    monkeypatch.setattr(aitools.subprocess, "run", process)
    with scope(config):
        assert aitools.get_playlist("a mood", 1) == "A - B"
    assert process.call_args.kwargs["timeout"] == 30
    assert process.call_args.args[0][-1] == "-"
    assert "a mood" in process.call_args.kwargs["input"]
