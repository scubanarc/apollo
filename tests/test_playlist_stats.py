import pytest

from apollo import estools
from apollo.jobs import run_worker


def test_stats_snapshot_counts_and_duration(service, config, monkeypatch):
    source = config.directory("PLAYLIST_SOURCE_FOLDER") / "test.txt"
    source.write_bytes(b"# untouched\r\n")
    calls = []
    monkeypatch.setattr(estools, "get_es", lambda: (None, "music"))

    def search(es, index, artist, title):
        calls.append((artist, title))
        durations = {"Last": 180, "First": 120, "Unknown": None}
        hits = (
            []
            if title == "Missing"
            else [
                {
                    "_source": {
                        "duration": durations[title],
                        "extension": ".flac",
                    }
                }
            ]
        )
        return {"hits": {"hits": hits}}

    monkeypatch.setattr(estools, "search_es", search)
    content = "\n# comment\n Z - Last  \nz – last\nA - First\nA - Missing\nB - Unknown\nbad line\n"
    result = service.execute("playlist.stats", {"name": "test", "content": content})
    assert result["song_count"] == 5
    assert result["unique_songs"] == 4
    assert result["duplicates"] == 1
    assert result["unique_artists"] == 3
    assert result["duration_seconds"] == 480
    assert result["unique_duration_seconds"] == 300
    assert result["timed_entries"] == 3
    assert result["matched_songs"] == 3
    assert result["unknown_duration_songs"] == 1
    assert result["unchecked_songs"] == 0
    assert result["missing_songs"] == ["A - Missing"]
    assert result["invalid_lines"] == [8]
    assert result["comment_lines"] == result["blank_lines"] == 1
    assert result["duplicate_songs"] == [{"song": "Z - Last", "count": 2}]
    assert result["formats"] == {"flac": 3}
    assert result["shortest"] == {"song": "A - First", "duration": 120}
    assert result["longest"] == {"song": "Z - Last", "duration": 180}
    assert len(calls) == 4
    assert source.read_bytes() == b"# untouched\r\n"


def test_stats_without_library_keeps_text_counts(service, monkeypatch):
    def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(estools, "get_es", unavailable)
    result = service.execute("playlist.stats", {"name": "test", "content": "A - B\nA - B"})
    assert result["song_count"] == 2
    assert result["duplicates"] == 1
    assert result["unchecked_songs"] == 1
    assert result["warning"]
    assert result["missing_songs"] == []
    empty = service.execute("playlist.stats", {"name": "test", "content": "# only comments"})
    assert empty["song_count"] == 0
    assert empty["warning"] is None


def test_stats_button_and_job(client, csrf, app, service, config):
    path = config.directory("PLAYLIST_SOURCE_FOLDER") / "test.txt"
    path.write_text("# source")
    page = client.get("/playlists/test")
    assert b">Stats</button>" in page.data
    payload = {"action": "playlist.stats", "payload": {"name": "test", "content": "# unsaved"}}
    assert client.post("/api/v1/jobs", json=payload).status_code == 400
    response = client.post("/api/v1/jobs", headers={"X-CSRF-Token": csrf}, json=payload)
    assert response.status_code == 202
    run_worker(service, app.extensions["apollo_jobs"], once=True)
    job = client.get(response.json["status_url"]).json
    assert job["status"] == "succeeded"
    assert job["result"]["comment_lines"] == 1
    assert path.read_text() == "# source"


@pytest.mark.parametrize("payload", [{"name": "test"}, {"name": "test", "content": None}])
def test_stats_invalid_payload(service, payload):
    with pytest.raises(ValueError):
        service.execute("playlist.stats", payload)


def test_genre_stats_count_repeats_and_merge_tags(service, monkeypatch):
    monkeypatch.setattr(estools, "get_es", lambda: (None, "music"))
    tags = {
        "One": " Rock ",
        "Two": ["rock", "ROCK", "Pop/Rock", "", None],
        "Three": None,
        "Four": [" ", 12],
    }

    def search(es, index, artist, title):
        hits = (
            []
            if title == "Missing"
            else [
                {
                    "_source": {
                        "genre": tags[title],
                        "extension": ".flac",
                        "duration": 60,
                    }
                }
            ]
        )
        return {"hits": {"hits": hits}}

    monkeypatch.setattr(estools, "search_es", search)
    result = service.execute(
        "playlist.stats",
        {"name": "test", "content": "A - One\nA - One\nA - Two\nA - Three\nA - Four\nA - Missing"},
    )
    assert result["genres"] == [
        {"genre": "Rock", "songs": 2, "entries": 3},
        {"genre": "Pop/Rock", "songs": 1, "entries": 1},
    ]
    assert result["genre_entries"] == 3
    assert result["shortest"]["song"] == "A - One"
    assert result["unknown_genre_songs"] == 2
    assert result["song_count"] == 6
    assert result["missing_songs"] == ["A - Missing"]


def test_genre_stats_partial_lookup(service, monkeypatch):
    monkeypatch.setattr(estools, "get_es", lambda: (None, "music"))

    def search(es, index, artist, title):
        if title == "Two":
            raise RuntimeError("offline")
        return {"hits": {"hits": [{"_source": {"genre": "Jazz", "extension": ".flac"}}]}}

    monkeypatch.setattr(estools, "search_es", search)
    result = service.execute("playlist.stats", {"name": "test", "content": "A - One\nA - Two"})
    assert result["genres"] == [{"genre": "Jazz", "songs": 1, "entries": 1}]
    assert result["genre_entries"] == 1
    assert result["unchecked_songs"] == 1
    assert result["unknown_genre_songs"] == 0
    assert result["warning"]
