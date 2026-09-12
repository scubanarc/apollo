"""Application use cases consumed by the CLI, web routes and workers."""

import fcntl
import json
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

from apollo import settings
from apollo.runtime import scope

logger = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    pass


OPERATIONS = {
    "playlist.preview": {"type", "input", "name"},
    "playlist.create": {"type", "input", "name", "dynamic"},
    "playlist.save": {"name", "tracks"},
    "playlist.replace": {"name", "content"},
    "playlist.stats": {"name", "content"},
    "playlist.publish": {"name", "all"},
    "playlist.play": {"name"},
    "song.play": {"song"},
    "entry.play": {"entry_id"},
    "library.scan": {"prune"},
    "library.compare": {"directory"},
    "ratings.calculate": {"artist", "title", "artists", "store"},
    "ratings.sync": set(),
}


def validate(action, payload):
    if not isinstance(action, str) or action not in OPERATIONS:
        raise ValueError("Unknown operation.")
    if not isinstance(payload, dict) or set(payload) - OPERATIONS[action]:
        raise ValueError("Unexpected operation fields.")
    for key, value in payload.items():
        if key in {"all", "dynamic", "prune", "artists", "store"}:
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean.")
        elif key == "content":
            if not isinstance(value, str):
                raise ValueError("content must be a string.")
        elif key == "tracks":
            if (
                not isinstance(value, list)
                or not value
                or len(value) > 10000
                or not all(isinstance(v, str) and len(v) <= 2000 for v in value)
            ):
                raise ValueError("tracks must contain between 1 and 10000 song strings.")
        elif not isinstance(value, str) or not value.strip() or len(value) > 10000:
            raise ValueError(f"{key} must be a nonempty string (maximum 10000 characters).")
    if action in {"playlist.preview", "playlist.create"}:
        if payload.get("type") not in {"ai", "artist", "path", "any"} or not payload.get("input"):
            raise ValueError("Choose a playlist type and provide input.")
        if payload["type"] == "any":
            try:
                re.compile(payload["input"])
            except re.error as exc:
                raise ValueError("Invalid metadata regular expression.") from exc
    if action == "playlist.save" and (not payload.get("name") or not payload.get("tracks")):
        raise ValueError("A playlist name and tracks are required.")
    if action in {"playlist.replace", "playlist.stats"} and (
        not payload.get("name") or "content" not in payload
    ):
        raise ValueError("A playlist name and content are required.")
    if action == "playlist.publish" and bool(payload.get("name")) == bool(payload.get("all")):
        raise ValueError("Choose one playlist or all playlists.")
    if action == "song.play":
        from apollo.playlist import normalize

        if not payload.get("song"):
            raise ValueError("A song is required.")
        normalize([payload["song"]])
    if action == "entry.play" and not payload.get("entry_id"):
        raise ValueError("An Elasticsearch entry ID is required.")
    if action == "library.compare" and not payload.get("directory"):
        raise ValueError("A comparison directory is required.")
    if action == "ratings.calculate":
        if payload.get("title") and not payload.get("artist"):
            raise ValueError("A title requires an artist.")
        if payload.get("artists") and payload.get("artist"):
            raise ValueError("Choose a single artist or all artists.")
        if payload.get("store") and (payload.get("artist") or payload.get("artists")):
            raise ValueError("Store applies to calculation of all songs.")
    return dict(payload)


@contextmanager
def operation_lock(config):
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (config.state_dir / "operation.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class ApolloService:
    def __init__(self, config: settings.Settings | None = None):
        self.config = config or settings.load_settings()

    def redact(self, message):
        text = str(message)
        for key, value in self.config.values.items():
            if (
                any(part in key for part in ("PWD", "PASSWORD", "TOKEN", "SECRET", "API_KEY"))
                and value
            ):
                text = text.replace(str(value), "[redacted]")
        text = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", text)
        text = re.sub(r"([?&](?:t|s|p|u|api_key|token)=)[^&\s]+", r"\1[redacted]", text)
        return text

    def execute(self, action, payload):
        payload = validate(action, payload)
        try:
            with operation_lock(self.config), scope(self.config):
                logger.info("Starting %s", action)
                result = self._execute(action, payload)
                logger.info("Finished %s", action)
                # A single JSON contract for terminal, API and persisted jobs.
                return json.loads(json.dumps(result, default=str))
        except (ValueError, FileNotFoundError) as exc:
            raise ServiceError(self.redact(exc)) from exc
        except Exception as exc:
            logger.debug("Operation %s failed (%s)", action, type(exc).__name__)
            raise ServiceError(
                f"{action} failed ({type(exc).__name__}). Check configuration, service access and directories with apollo doctor."
            ) from exc

    def _execute(self, action, p):
        from apollo import compare, mpd_client, navidrome, playback, playlist, ratings, scanner

        if action == "playlist.stats":
            from apollo.playlist_stats import calculate

            playlist.source_path(p["name"])
            return calculate(p["content"])
        if action == "playlist.preview":
            return playlist.preview(p["type"], p["input"], p.get("name"))
        if action == "playlist.create":
            return playlist.create_playlist(
                p["type"], p["input"], p.get("name"), p.get("dynamic", False)
            )
        if action == "playlist.save":
            return playlist.save_tracks(p["name"], p["tracks"])
        if action == "playlist.replace":
            return playlist.replace_source(p["name"], p["content"])
        if action == "playlist.publish":
            return playlist.write_m3u_files(p.get("name"))
        if action == "playlist.play":
            return playback.play(p.get("name"))
        if action == "entry.play":
            return mpd_client.play_entry(p["entry_id"])
        if action == "song.play":
            return mpd_client.play_song(p["song"])
        if action == "library.scan":
            return scanner.scan_music_folder_into_es(p.get("prune", False))
        if action == "library.compare":
            return compare.compare_directory(p["directory"])
        if action == "ratings.sync":
            return navidrome.update_all_ratings()
        if action == "ratings.calculate":
            if p.get("artist"):
                return (
                    ratings.calculate_rating(p["artist"], p["title"])
                    if p.get("title")
                    else ratings.calculate_artist_rating(p["artist"])
                )
            if p.get("artists"):
                rows = ratings.calculate_all_artists_ratings()
            else:
                rows = sorted(
                    ratings.calculate_all_ratings().values(),
                    key=lambda r: r["calculated_rating"],
                    reverse=True,
                )
            result = dict(items=rows, total=len(rows))
            if p.get("store"):
                result.update(ratings.store_calculated_ratings())
            return result
        raise ValueError("Unknown operation.")

    def stream_path(self, *, song=None, entry_id=None):
        """Resolve an indexed audio file within the configured music directory."""
        from elasticsearch import NotFoundError

        from apollo import estools, mpd_client, playlist

        if bool(song) == bool(entry_id):
            raise ValueError("Provide exactly one song or entry_id.")
        validate(
            "entry.play" if entry_id else "song.play",
            {"entry_id": entry_id} if entry_id else {"song": song},
        )
        if song and not playlist.normalize([song]):
            raise ValueError("A playable song is required.")
        try:
            with scope(self.config):
                es, index = estools.get_es()
                if entry_id:
                    source = es.get(index=index, id=entry_id)["_source"]
                else:
                    artist, title = playlist.normalize([song])[0].split(" - ", 1)
                    best, _, _ = estools.pick_best_hit(estools.search_es(es, index, artist, title))
                    if best is None:
                        raise FileNotFoundError("Song not found.")
                    source = best["hit"]["_source"]
                root = self.config.directory("MUSIC_FOLDER").resolve()
                path = root / mpd_client._song_uri(source.get("url", ""), root)
                if not path.is_file():
                    raise FileNotFoundError("Audio file not found.")
                if path.suffix.lower() not in {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".aif", ".aiff", ".wma"}:
                    raise ValueError("The selected file is not a supported audio file.")
                return path
        except NotFoundError as exc:
            raise FileNotFoundError("Library entry not found.") from exc
        except (ValueError, FileNotFoundError):
            raise
        except Exception as exc:
            raise ServiceError("Audio lookup unavailable. Try again later.") from exc

    def read(self, resource, limit=50, offset=0, name=None):
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1–200 and offset must be nonnegative.")
        try:
            with scope(self.config):
                from apollo import playlist, ratings

                if resource == "song":
                    from apollo.song_details import details

                    return details(name)
                if resource == "playlists":
                    return playlist.list_playlists(limit, offset)
                if resource == "playlist.source":
                    path = playlist.source_path(name)
                    with path.open(encoding="utf-8", newline="") as stream:
                        content = stream.read()
                    return dict(
                        name=name.removesuffix(".txt"),
                        content=content,
                        tracks=playlist.playable_tracks(content.splitlines()),
                    )
                if resource == "playlist":
                    path = playlist.source_path(name)
                    tracks = playlist.normalize(path.read_text().splitlines())
                    return dict(
                        name=name,
                        items=tracks[offset : offset + limit],
                        total=len(tracks),
                        limit=limit,
                        offset=offset,
                    )
                if resource in {"ratings", "votes", "skips"}:
                    return json.loads(
                        json.dumps(ratings.records(resource, limit, offset), default=str)
                    )
                raise ValueError("Unknown resource.")
        except ValueError:
            raise
        except FileNotFoundError as exc:
            raise ServiceError("Playlist does not exist.") from exc
        except Exception as exc:
            raise ServiceError(
                f"{resource} unavailable ({type(exc).__name__}). Run apollo doctor for diagnostics."
            ) from exc

    def status(self, check_services=False):
        checks = []
        for key in ("MUSIC_FOLDER", "PLAYLIST_SOURCE_FOLDER", "PLAYLIST_PUBLISHED_FOLDER"):
            value = self.config.get(key)
            if key == "PLAYLIST_PUBLISHED_FOLDER" and not value:
                checks.append(
                    dict(name=key, ok=True, detail="Using generated output in .apollo/m3u")
                )
                continue
            checks.append(
                dict(
                    name=key,
                    ok=bool(value and Path(value).expanduser().is_dir()),
                    detail="Directory available"
                    if value and Path(value).expanduser().is_dir()
                    else "Not configured or unavailable",
                )
            )
        groups = {
            "Elasticsearch": ["ES_URL"],
            "MySQL": ["DATABASE_HOST", "DATABASE_UN", "DATABASE_PWD", "DATABASE_NAME"],
            "Navidrome": ["NAVIDROME_URL", "NAVIDROME_UN", "NAVIDROME_PWD"],
            "MPD": ["MPD_HOST", "MPD_PORT"],
            "MQTT": ["MQTT_SERVER", "MQTT_PORT", "MQTT_UN", "MQTT_PWD"],
        }
        groups["AI"] = (
            ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"]
            if self.config.get("AI_PROVIDER") == "openai"
            else []
        )
        for name, keys in groups.items():
            missing = [key for key in keys if not self.config.get(key)]
            checks.append(
                dict(
                    name=name,
                    ok=not missing,
                    detail="Configured; connection not checked"
                    if not missing
                    else "Missing: " + ", ".join(missing),
                )
            )
        if self.config.get("AI_PROVIDER") == "codex":
            from apollo.aitools import _resolve_codex_bin

            check = next(c for c in checks if c["name"] == "AI")
            try:
                with scope(self.config):
                    _resolve_codex_bin()
                check["detail"] = "Codex executable available; generation not checked"
            except RuntimeError:
                check.update(
                    ok=False, detail="Codex executable unavailable; configure CODEX_BIN or PATH"
                )
        checks.append(
            dict(
                name="ffprobe",
                ok=bool(shutil.which("ffprobe")),
                detail="Available" if shutil.which("ffprobe") else "Install ffmpeg",
            )
        )
        if check_services:
            from apollo import estools, mpd_client, navidrome, ratings

            def mysql():
                _, cursor = ratings.get_db_connection()
                cursor.execute("SELECT 1")

            for name, probe in [
                ("Elasticsearch", lambda: estools.get_es()[0].info()),
                ("MySQL", mysql),
                ("Navidrome", lambda: navidrome._subsonic_get("ping", timeout=5)),
                ("MPD", mpd_client.ping),
            ]:
                check = next(c for c in checks if c["name"] == name)
                if check["ok"]:
                    try:
                        with scope(self.config):
                            probe()
                        check["detail"] = "Connection successful"
                    except Exception as exc:
                        check.update(ok=False, detail=f"Connection failed ({type(exc).__name__})")
        return dict(
            checks=checks,
            config_file=str(self.config.path),
            configured=self.config.path.exists(),
            ok=all(c["ok"] for c in checks),
        )
