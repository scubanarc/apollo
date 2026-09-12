from unittest.mock import MagicMock

import pytest

from apollo import estools, mpd_client, ratings, song_details
from apollo.runtime import scope


def hit(identifier, score=10, extension=".mp3", bitrate=320000):
    return {
        "_id": identifier,
        "_score": score,
        "_source": {
            "artist": "Artist",
            "title": "<Song>",
            "extension": extension,
            "bitrate": bitrate,
            "url": "/music/" + identifier + extension,
            "duration": 123,
            "samplerate": 44100,
        },
    }


@pytest.fixture
def matches(monkeypatch):
    rows = [
        hit("mp3"),
        hit("flac", extension=".flac", bitrate=900000),
        hit("low-score", score=9, extension=".flac", bitrate=1200000),
    ]
    rows += [hit(str(n), score=8) for n in range(12)]
    monkeypatch.setattr(estools, "get_es", lambda: (None, "music"))
    monkeypatch.setattr(estools, "search_es", lambda *args: {"hits": {"hits": rows[:10]}})
    monkeypatch.setattr(song_details, "scan", lambda *args, **kwargs: iter(rows))
    monkeypatch.setattr(
        ratings,
        "calculate_rating",
        lambda *args: {
            "rating": 2,
            "good_votes": 1,
            "bad_votes": 2,
            "skips": 3,
            "calculated_rating": 32,
        },
    )
    return rows


def test_details_lists_all_and_explains_real_winner(client, matches):
    response = client.get("/api/v1/song", query_string={"song": "Artist - <Song>"})
    assert response.status_code == 200
    result = response.json
    assert result["total"] == 15
    assert result["winner_id"] == "flac"
    assert result["excluded"] is True
    entries = {e["id"]: e for e in result["entries"]}
    assert "FLAC is preferred" in entries["mp3"]["reason"]
    assert "Lower search relevance" in entries["low-score"]["reason"]
    assert "Outside the first 10" in entries["11"]["reason"]
    page = client.get("/song", query_string={"song": "Artist - <Song>"})
    assert page.status_code == 200
    assert b"&lt;Song&gt;" in page.data
    assert b"<Song>" not in page.data
    assert page.data.count(b'action="/actions/entry.play"') == 15
    assert b"900.0 kbps" in page.data


def test_rating_outage_preserves_entries(client, matches, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("private credentials")

    monkeypatch.setattr(ratings, "calculate_rating", unavailable)
    result = client.get("/api/v1/song", query_string={"song": "Artist - Song"}).json
    assert result["total"] == 15
    assert result["rating"] is None
    assert "unavailable" in result["rating_warning"]
    assert "private credentials" not in str(result)


@pytest.mark.parametrize("song", ["", "# comment", "invalid"])
def test_invalid_song(client, song):
    assert client.get("/api/v1/song", query_string={"song": song}).status_code == 400


def test_empty_matches(client, matches):
    matches.clear()
    result = client.get("/api/v1/song", query_string={"song": "A - B"}).json
    assert result["total"] == 0
    assert result["winner_id"] is None


def test_exact_entry_playback_and_path_boundary(config, monkeypatch):
    es = MagicMock()
    source = {
        "url": str(config.directory("MUSIC_FOLDER") / "specific.flac"),
        "artist": "A",
        "title": "B",
    }
    es.get.return_value = {"_source": source}
    monkeypatch.setattr(estools, "get_es", lambda: (es, "music"))
    connection = MagicMock()
    monkeypatch.setattr(mpd_client, "MPDConnection", connection)
    monkeypatch.setitem(config.values, "MPD_HOST", "test")
    monkeypatch.setitem(config.values, "MPD_PORT", 6600)
    with scope(config):
        result = mpd_client.play_entry("exact-id")
        assert result["uri"] == "specific.flac"
        es.get.assert_called_once_with(index="music", id="exact-id")
        connection.return_value.__enter__.return_value.add_next.assert_called_once_with(
            "specific.flac"
        )
        source["url"] = "/outside/file.flac"
        with pytest.raises(ValueError, match="outside MUSIC_FOLDER"):
            mpd_client.play_entry("exact-id")


def test_entry_play_requires_csrf_and_id(client, csrf):
    assert client.post("/actions/entry.play", data={"entry_id": "id"}).status_code == 400
    assert client.post("/actions/entry.play", data={"csrf_token": csrf}).status_code == 400
    assert (
        client.post("/actions/entry.play", data={"csrf_token": csrf, "entry_id": "id"}).status_code
        == 303
    )
