"""Small MPD protocol client for inserting one library song into playback."""

import socket
from pathlib import Path

from apollo import estools, playlist, settings


class MPDError(RuntimeError):
    pass


def _quote(value):
    value = str(value)
    if any(character in value for character in "\r\n\0"):
        raise ValueError("MPD command arguments cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MPDConnection:
    """The subset of MPD's line protocol needed by Apollo."""

    def __init__(self, host, port, timeout=5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.stream = None

    def __enter__(self):
        self.socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.stream = self.socket.makefile("rwb")
        greeting = self.stream.readline().decode("utf-8", errors="replace").rstrip("\r\n")
        if not greeting.startswith("OK MPD "):
            self.close()
            raise MPDError("The server did not provide an MPD greeting.")
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def command(self, command):
        self.stream.write((command + "\n").encode("utf-8"))
        self.stream.flush()
        response = []
        while True:
            raw = self.stream.readline()
            if not raw:
                raise MPDError("The MPD connection closed before responding.")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "OK":
                return response
            if line.startswith("ACK "):
                raise MPDError(line)
            response.append(line)

    def add_next(self, uri):
        response = self.command(f"addid {_quote(uri)} +0")
        identifiers = [line.removeprefix("Id: ") for line in response if line.startswith("Id: ")]
        if len(identifiers) != 1:
            raise MPDError("MPD did not return the queued song id.")
        self.command("next")
        return identifiers[0]

    def ping(self):
        self.command("ping")


def _song_uri(url, music_folder):
    path = Path(url)
    if not path.is_absolute():
        raise ValueError("The selected library song does not have an absolute file path.")
    path = path.resolve(strict=False)
    music_folder = music_folder.resolve()
    try:
        relative = path.relative_to(music_folder)
    except ValueError as exc:
        raise ValueError("The selected library song is outside MUSIC_FOLDER.") from exc
    if not relative.parts:
        raise ValueError("The selected library song does not identify a file.")
    return relative.as_posix()


def play_song(song):
    """Resolve a song, queue it after the current MPD song, and skip to it."""
    normalized = playlist.normalize([song])[0]
    artist, title = normalized.split(" - ", 1)
    es, index = estools.get_es()
    best, _, _ = estools.pick_best_hit(estools.search_es(es, index, artist, title))
    if best is None:
        raise ValueError(f"Song not found in the music library: {normalized}")
    config = settings.current()
    uri = _song_uri(best["hit"]["_source"].get("url", ""), config.directory("MUSIC_FOLDER"))
    with MPDConnection(config.require("MPD_HOST"), config.require("MPD_PORT")) as client:
        song_id = client.add_next(uri)
    return {"played": True, "song": normalized, "uri": uri, "mpd_song_id": song_id}


def play_entry(entry_id):
    """Play the exact indexed file, retaining the music-folder boundary check."""
    es, index = estools.get_es()
    source = es.get(index=index, id=entry_id)["_source"]
    config = settings.current()
    uri = _song_uri(source.get("url", ""), config.directory("MUSIC_FOLDER"))
    with MPDConnection(config.require("MPD_HOST"), config.require("MPD_PORT")) as client:
        song_id = client.add_next(uri)
    return {
        "played": True,
        "song": f"{source.get('artist', '')} - {source.get('title', '')}",
        "uri": uri,
        "mpd_song_id": song_id,
        "entry_id": entry_id,
    }


def ping():
    config = settings.current()
    with MPDConnection(config.require("MPD_HOST"), config.require("MPD_PORT")) as client:
        client.ping()
