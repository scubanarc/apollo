from pathlib import Path
from unittest.mock import Mock

import pytest

from apollo import compare, estools, mpd_client, playback, playlist, ratings, scanner
from apollo.runtime import scope
from apollo.services import ServiceError
from apollo.settings import ConfigurationError, Settings, load_settings


def test_config_defaults_and_validation(tmp_path):
    path = tmp_path / "missing.yml"
    assert load_settings(path).get("RATING_THRESHOLD") == 45
    path.write_text("- bad\n")
    with pytest.raises(ConfigurationError):
        load_settings(path)
    path.write_text("VOTE_STRENGTH: wrong\n")
    with pytest.raises(ConfigurationError):
        load_settings(path)


def test_shared_config_selection_and_credentials(tmp_path, monkeypatch):
    from apollo import settings

    shared = tmp_path / "settings.yml"
    shared.write_text("API_TOKEN: yaml-token\n")
    (tmp_path / "apollo.env").write_text('APOLLO_API_TOKEN="file-token"\n')
    monkeypatch.setattr(settings, "DEFAULT_CONFIG", shared)
    monkeypatch.delenv("APOLLO_CONFIG", raising=False)
    monkeypatch.delenv("APOLLO_API_TOKEN", raising=False)
    config = load_settings()
    assert config.path == shared
    assert config.get("API_TOKEN") == "file-token"

    from apollo.services import ApolloService
    from apollo.web import create_app

    monkeypatch.setenv("APOLLO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APOLLO_API_TOKEN", "environment-token")
    assert create_app(ApolloService(config)).config["API_TOKEN"] == "environment-token"
    alternate = tmp_path / "alternate" / "settings.yml"
    alternate.parent.mkdir()
    alternate.write_text("API_TOKEN: alternate-token\n")
    monkeypatch.setenv("APOLLO_CONFIG", str(alternate))
    assert load_settings().get("API_TOKEN") == "alternate-token"
    assert load_settings(shared).path == shared


def test_move_sources_preserves_generated_outputs(config, tmp_path, monkeypatch):
    from apollo import settings

    old_source = config.directory("PLAYLIST_SOURCE_FOLDER")
    work = old_source / ".apollo"
    (work / "ai").mkdir(parents=True)
    (work / "ai/es.jsonl").write_text('{"artist": "Artist", "title": "Song"}\n')
    new_source = tmp_path / "shared"
    new_source.mkdir()
    (new_source / "mix.txt").write_text("Artist - Song\n")
    moved = Settings(
        dict(config.values, PLAYLIST_SOURCE_FOLDER=str(new_source), PLAYLIST_WORK_FOLDER=str(work)),
        config.path,
    )
    monkeypatch.setattr(estools, "get_es", lambda: (None, "test"))
    monkeypatch.setattr(
        estools, "get_playlist_from_lines", lambda *args: (["/music/song.flac"], [], 10, [])
    )
    with scope(moved):
        assert playlist.get_tracks_by_type("any", "Artist") == ["Artist - Song"]
        playlist.write_m3u_files("mix")
        assert Path(settings.get_apollo_folders()[0]) == new_source
    assert (work / "m3u/mix.m3u").is_file()
    assert (config.directory("PLAYLIST_PUBLISHED_FOLDER") / "mix.m3u").is_file()
    assert not (new_source / ".apollo").exists()


@pytest.mark.parametrize("content", ["", "BITRATE_MULTIPLIERS:\n", "BITRATE_MULTIPLIERS: null\n"])
def test_unset_bitrate_multipliers_use_defaults(tmp_path, content):
    path = tmp_path / "settings.yml"
    path.write_text(content)
    config = load_settings(path)
    with scope(config):
        assert estools.get_normalized_bitrate(100, ".ogg") == 130
        assert ratings.rating_formula(4, 0, 0, 0) == 80


@pytest.mark.parametrize("value", ["[]", "{'.ogg': wrong}", "{'.ogg': 0}"])
def test_invalid_bitrate_multipliers_still_rejected(tmp_path, value):
    path = tmp_path / "settings.yml"
    path.write_text(f"BITRATE_MULTIPLIERS: {value}\n")
    with pytest.raises(ConfigurationError, match="BITRATE_MULTIPLIERS"):
        load_settings(path)


@pytest.mark.parametrize("value", ["'1883'", "true", "0", "65536"])
def test_invalid_mqtt_port_rejected(tmp_path, value):
    path = tmp_path / "settings.yml"
    path.write_text(f"MQTT_PORT: {value}\n")
    with pytest.raises(ConfigurationError, match="MQTT_PORT"):
        load_settings(path)


@pytest.mark.parametrize("value", ["'6600'", "true", "0", "65536"])
def test_invalid_mpd_port_rejected(tmp_path, value):
    path = tmp_path / "settings.yml"
    path.write_text(f"MPD_PORT: {value}\n")
    with pytest.raises(ConfigurationError, match="MPD_PORT"):
        load_settings(path)


def test_song_identity():
    assert playlist.normalize(
        [
            "AC-DC - T.N.T.",
            "AC-DC - T.N.T.",
            "Drake – Wants and Needs (feat. Lil Baby)",
            "Artist — Title",
            "# comment",
            "",
        ]
    ) == ["AC-DC - T.N.T.", "Artist - Title", "Drake - Wants and Needs (feat. Lil Baby)"]
    with pytest.raises(ValueError):
        playlist.normalize(["bad input"])

    assert playlist.playable_tracks(
        ["B – Two", "# comment", "bad input", "A - One", "B - Two"]
    ) == ["B - Two", "A - One", "B - Two"]


def test_playlists_sort_letters_then_numbers_naturally(config):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    for name in ["10", "zebra", "2", "Alpha", "_special"]:
        (source / f"{name}.txt").write_text("A - B\n")

    with scope(config):
        names = [row["name"] for row in playlist.list_playlists()["items"]]

    assert names == [
        "Alpha",
        "zebra",
        "2",
        "10",
        "_special",
    ]


def test_blank_playlist_name_uses_configured_dynamic_playlist(config, monkeypatch):
    config = Settings(
        dict(config.values, DEFAULT_PLAYLIST_FILE="random", DYNAMIC_PLAYLIST_FILE="now-playing"),
        config.path,
    )
    monkeypatch.setattr(playlist, "get_tracks_by_type", lambda *_: ["Artist - Song"])

    with scope(config):
        result = playlist.preview("artist", "Artist")

    assert result["name"] == "now-playing"


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", ".apollo/ai/hack", "folder/../../escape", "x\\escape"]
)
def test_path_containment(service, name):
    with pytest.raises(ServiceError):
        service.execute("playlist.save", {"name": name, "tracks": ["A - B"]})


def test_symlink_escape(service, config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.directory("PLAYLIST_SOURCE_FOLDER") / "linked").symlink_to(
        outside, target_is_directory=True
    )
    with pytest.raises(ServiceError):
        service.execute("playlist.save", {"name": "linked/mix", "tracks": ["A - B"]})
    assert not list(outside.iterdir())


def test_save_dedup_and_nested_names(service, config):
    result = service.execute(
        "playlist.save", {"name": "sets/night", "tracks": ["A - B", "a - b", "C - D"]}
    )
    assert result["added"] == 2
    result = service.execute("playlist.save", {"name": "sets/night", "tracks": ["a - b", "E - F"]})
    assert result["added"] == 1 and result["total"] == 3
    assert service.read("playlists")["items"] == [{"name": "sets/night"}]


def test_all_playlist_mutations_offer_playback(service, config):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    saved = service.execute("playlist.save", {"name": "dynamic", "tracks": ["A - B"]})
    assert saved["play_now_name"] == "dynamic"
    replaced = service.execute("playlist.replace", {"name": "dynamic", "content": "C - D\n"})
    assert replaced["play_now_name"] == "dynamic"
    ordinary = service.execute("playlist.save", {"name": "mix", "tracks": ["A - B"]})
    assert ordinary["play_now_name"] == "mix"
    assert (source / "dynamic.txt").read_text() == "C - D\n"


def test_publish_outputs(service, config, monkeypatch):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    (source / "mix.txt").write_text("AC-DC - Thunder\nMissing - Song\n")
    (source / ".apollo/sorted").mkdir(parents=True)
    (source / ".apollo/sorted/stale.txt").write_text("Old - Song")
    monkeypatch.setattr(estools, "get_es", lambda: (None, "test"))
    monkeypatch.setattr(
        estools,
        "get_playlist_from_lines",
        lambda es, index, lines: (["/music/a.flac"], ["AC-DC - Thunder"], 123, ["Missing - Song"]),
    )
    result = service.execute("playlist.publish", {"all": True})
    assert result["total"] == 1
    assert (
        config.directory("PLAYLIST_PUBLISHED_FOLDER") / "mix.m3u"
    ).read_text() == "#EXTM3U\n/music/a.flac\n"
    assert (
        config.directory("PLAYLIST_PUBLISHED_FOLDER") / "mix.m3u"
    ).stat().st_mode & 0o777 == 0o644
    assert (source / ".apollo/missing/mix.txt").read_text() == "Missing - Song"
    assert not (config.directory("PLAYLIST_PUBLISHED_FOLDER") / "stale.m3u").exists()


def test_play_playlist_publishes_before_mqtt(service, config, monkeypatch):
    configured = Settings(
        dict(
            config.values,
            MQTT_SERVER="broker.example",
            MQTT_PORT=1883,
            MQTT_UN="mqtt-user",
            MQTT_PWD="mqtt-password",
        ),
        config.path,
    )
    service.config = configured
    source = configured.directory("PLAYLIST_SOURCE_FOLDER")
    (source / "1970.txt").write_text("A - B\n")
    monkeypatch.setattr(estools, "get_es", lambda: (None, "test"))
    monkeypatch.setattr(
        estools,
        "get_playlist_from_lines",
        lambda *args: (["/music/a.flac"], ["A - B"], 10, []),
    )
    calls = []

    def publish_single(*args, **kwargs):
        published = configured.directory("PLAYLIST_PUBLISHED_FOLDER") / "1970.m3u"
        calls.append((published.read_text(), args, kwargs))

    monkeypatch.setattr(playback.publish, "single", publish_single)
    result = service.execute("playlist.play", {"name": "1970"})

    assert result["played"] is True
    assert result["topic"] == "nodered/playlist"
    assert result["payload"] == "1970"
    assert calls == [
        (
            "#EXTM3U\n/music/a.flac\n",
            ("nodered/playlist",),
            {
                "payload": "1970",
                "hostname": "broker.example",
                "port": 1883,
                "auth": {"username": "mqtt-user", "password": "mqtt-password"},
            },
        )
    ]


def test_play_song_inserts_after_current_song_and_skips(config, monkeypatch):
    configured = Settings(dict(config.values, MPD_HOST="mpd.example", MPD_PORT=6600), config.path)
    path = configured.directory("MUSIC_FOLDER") / 'Artist/Album/01 - Say "Hi".flac'
    path.parent.mkdir(parents=True)
    path.write_text("test")
    monkeypatch.setattr(estools, "get_es", lambda: (None, "test"))
    monkeypatch.setattr(
        estools,
        "search_es",
        lambda *args: {
            "hits": {
                "hits": [
                    hit(".flac", 1000)
                    | {
                        "_source": {
                            **hit(".flac", 1000)["_source"],
                            "artist": "Artist",
                            "title": "Song",
                            "url": str(path),
                        }
                    }
                ]
            }
        },
    )
    calls = []

    class FakeConnection:
        def __init__(self, host, port):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def add_next(self, uri):
            calls.append(("add_next", uri))
            return "42"

    monkeypatch.setattr(mpd_client, "MPDConnection", FakeConnection)
    with scope(configured):
        result = mpd_client.play_song("Artist - Song")

    assert calls == [
        ("connect", "mpd.example", 6600),
        ("add_next", 'Artist/Album/01 - Say "Hi".flac'),
    ]
    assert result == {
        "played": True,
        "song": "Artist - Song",
        "uri": 'Artist/Album/01 - Say "Hi".flac',
        "mpd_song_id": "42",
    }


def test_mpd_add_next_uses_relative_queue_position(monkeypatch):
    client = mpd_client.MPDConnection("unused", 6600)
    commands = []

    def command(value):
        commands.append(value)
        return ["Id: 9"] if value.startswith("addid ") else []

    monkeypatch.setattr(client, "command", command)
    assert client.add_next('Artist/01 - Say "Hi".flac') == "9"
    assert commands == ['addid "Artist/01 - Say \\"Hi\\".flac" +0', "next"]


def hit(extension, bitrate, score=1):
    return {
        "_score": score,
        "_source": {
            "extension": extension,
            "bitrate": bitrate,
            "artist": "AC-DC",
            "title": "Thunder",
            "url": "/music/song" + extension,
            "duration": 10,
        },
    }


def test_selection_and_hyphenated_artist(config, monkeypatch):
    with scope(config):
        result = {"hits": {"hits": [hit(".mp3", 320000), hit(".flac", 200000)]}}
        assert estools.pick_best_hit(result)[0]["hit"]["_source"]["extension"] == ".flac"
        result = {"hits": {"hits": [hit(".mp3", 300000), hit(".ogg", 250000)]}}
        assert estools.pick_best_hit(result)[0]["hit"]["_source"]["extension"] == ".ogg"
        lookup = Mock(return_value=result)
        monkeypatch.setattr(estools, "search_es", lookup)
        monkeypatch.setattr(ratings, "get_calculated_rating", lambda a, t: 50)
        estools.get_playlist_from_lines(None, "test", ["AC-DC - Thunder"])
        lookup.assert_called_once_with(None, "test", "AC-DC", "Thunder")


def test_rating_formula_is_operation_scoped(config):
    with scope(config):
        assert ratings.rating_formula(None, 2, 1, 3) == 52
    changed = Settings(dict(config.values, VOTE_STRENGTH=10), config.path)
    with scope(changed):
        assert ratings.rating_formula(None, 2, 1, 3) == 57
    with scope(config):
        assert ratings.rating_formula(None, 2, 1, 3) == 52


def test_empty_library_never_connects_or_prunes(service, monkeypatch):
    es = Mock()
    monkeypatch.setattr(estools, "get_es", es)
    with pytest.raises(ServiceError, match="No supported music"):
        service.execute("library.scan", {"prune": True})
    es.assert_not_called()


def test_prune_bounded_to_root(config, monkeypatch):
    root = config.directory("MUSIC_FOLDER")
    (root / "present.mp3").write_text("test")
    es = Mock()
    monkeypatch.setattr(
        scanner,
        "scan",
        lambda *a, **k: [{"_id": str(root / "missing.mp3")}, {"_id": "/outside/song.mp3"}],
    )
    with scope(config):
        assert (
            scanner.prune_missing_files_from_es(root, {str(root / "present.mp3")}, es, "test") == 1
        )
    es.delete.assert_called_once_with(index="test", id=str(root / "missing.mp3"))


def test_prune_changed_library(config, monkeypatch):
    root = config.directory("MUSIC_FOLDER")
    (root / "new.mp3").write_text("test")
    es = Mock()
    with scope(config), pytest.raises(ValueError, match="changed"):
        scanner.prune_missing_files_from_es(root, {str(root / "old.mp3")}, es, "test")
    es.delete.assert_not_called()


def test_scan_metadata_errors_skip_prune(service, config, monkeypatch):
    root = config.directory("MUSIC_FOLDER")
    (root / "bad.mp3").write_text("test")
    es = Mock()
    monkeypatch.setattr(estools, "get_es", lambda: (es, "test"))
    monkeypatch.setattr(scanner, "scan", lambda *a, **k: [])
    result = service.execute("library.scan", {"prune": True})
    assert result["prune_skipped"] and result["errors"]
    es.delete.assert_not_called()


def test_compare_bitrate(service, config, monkeypatch):
    root = config.directory("MUSIC_FOLDER")
    path = root / "song.mp3"
    path.write_text("test")
    monkeypatch.setattr(
        compare, "metadata", lambda p: dict(artist="A", title="B", extension=".mp3", bitrate=320000)
    )
    monkeypatch.setattr(estools, "get_es", lambda: (None, "test"))
    monkeypatch.setattr(estools, "search_es", lambda *a: {"hits": {"hits": [hit(".mp3", 128000)]}})
    result = service.execute("library.compare", {"directory": str(root)})
    assert result["items"][0]["reason"] == "Higher normalized bitrate"


def test_connection_closed_and_ratings_refresh(config, monkeypatch):
    config = Settings(
        dict(
            config.values,
            DATABASE_HOST="test",
            DATABASE_UN="test",
            DATABASE_PWD="test",
            DATABASE_NAME="test",
        ),
        config.path,
    )
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchall.side_effect = [
        [{"artist": "A", "title": "B", "rating": 4}],
        [],
        [],
        [{"artist": "A", "title": "B", "rating": 2}],
        [],
        [],
    ]
    monkeypatch.setattr(ratings.pymysql, "connect", lambda **kw: connection)
    with scope(config):
        assert ratings.get_calculated_rating("A", "B") == 80
        assert ratings.get_calculated_rating("A", "B") == 80
    connection.close.assert_called_once()
    with scope(config):
        assert ratings.get_calculated_rating("A", "B") == 40
    assert connection.close.call_count == 2


def test_incremental_scan_skips_unchanged(service, config, monkeypatch):
    root = config.directory("MUSIC_FOLDER")
    path = root / "song.mp3"
    path.write_text("test")
    stat = path.stat()
    existing = dict(
        artist="A",
        title="B",
        size=stat.st_size,
        modification_time=stat.st_mtime,
        bitrate=320000,
        url=str(path),
    )
    es = Mock()
    monkeypatch.setattr(estools, "get_es", lambda: (es, "test"))
    monkeypatch.setattr(scanner, "scan", lambda *a, **k: [{"_id": str(path), "_source": existing}])
    reader = Mock()
    monkeypatch.setattr(scanner, "metadata", reader)
    result = service.execute("library.scan", {})
    assert result["unchanged"] == 1 and result["updated"] == 0
    reader.assert_not_called()
    es.update.assert_not_called()


def test_priority_tiebreak(config):
    config.path.with_name("priority.yml").write_text(
        "patterns:\n  - pattern: preferred\n    applies_to: [url]\n    weight: 10\n"
    )
    first, second = hit(".flac", 500000), hit(".flac", 500000)
    second["_source"]["url"] = "/preferred/song.flac"
    with scope(config):
        assert estools.pick_best_hit({"hits": {"hits": [first, second]}})[0]["hit"] == second


def test_low_ratings_filtered(config, monkeypatch):
    search = Mock()
    monkeypatch.setattr(estools, "search_es", search)
    monkeypatch.setattr(ratings, "get_calculated_rating", lambda *_: 40)
    with scope(config):
        assert estools.get_playlist_from_lines(None, "test", ["Artist - Song"])[0] == []
    search.assert_not_called()


def test_database_store_rolls_back(config, monkeypatch):
    db, cursor = Mock(), Mock()
    cursor.executemany.side_effect = RuntimeError("failed write")
    monkeypatch.setattr(ratings, "get_db_connection", lambda: (db, cursor))
    monkeypatch.setattr(
        ratings,
        "calculate_all_ratings",
        lambda: {
            ("A", "B"): dict(
                artist="A",
                title="B",
                rating=3,
                calculated_rating=60,
                good_votes=0,
                bad_votes=0,
                skips=0,
            )
        },
    )
    with scope(config), pytest.raises(RuntimeError):
        ratings.store_calculated_ratings()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
