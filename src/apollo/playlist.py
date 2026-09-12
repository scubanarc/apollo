"""Playlist generation, preview and publication without terminal interaction."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from apollo import aitools, estools, settings
from apollo.files import atomic_write, contained

TRACK_SEPARATOR = re.compile(r"\s+[-–—]\s+")


def source_path(name):
    name = name.removesuffix(".txt")
    return contained(settings.current().directory("PLAYLIST_SOURCE_FOLDER"), name + ".txt")


def normalize(lines):
    tracks = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = TRACK_SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(f"Expected artist - title: {line[:100]}")
        artist, title = parts
        line = f"{artist.strip()} - {title.strip()}"
        tracks.setdefault(line.casefold(), line)
    return sorted(tracks.values(), key=str.casefold)


def playable_tracks(lines):
    """Return valid source tracks in their original order, including repeats."""
    tracks = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = TRACK_SEPARATOR.split(line, maxsplit=1)
        if len(parts) == 2 and all(part.strip() for part in parts):
            tracks.append(f"{parts[0].strip()} - {parts[1].strip()}")
    return tracks


def get_tracks_by_type(ptype, input_str):
    if ptype == "ai":
        lines = normalize(aitools.get_playlist(input_str, 50).splitlines())
        es, index = estools.get_es()
        _, tracks, _, _ = estools.get_playlist_from_lines(es, index, lines)
        return normalize(tracks)
    if ptype in {"artist", "path"}:
        es, index = estools.get_es()
        lookup = estools.get_all_by_artist if ptype == "artist" else estools.get_all_by_path
        return normalize(lookup(es, index, input_str))
    if ptype == "any":
        pattern = re.compile(input_str, re.IGNORECASE)
        source = Path(settings.get_apollo_folders()[2]) / "es.jsonl"
        if not source.is_file():
            raise ValueError("Scan the library first to create the metadata search cache.")
        tracks = []
        with source.open(encoding="utf-8") as stream:
            for line in stream:
                if pattern.search(line):
                    row = json.loads(line)
                    if row.get("artist") and row.get("title"):
                        tracks.append(f"{row['artist']} - {row['title']}")
        return normalize(tracks)
    raise ValueError("Unknown playlist type.")


def preview(ptype, input_str, name=None):
    name = name or settings.get_setting("DYNAMIC_PLAYLIST_FILE")
    path = source_path(name)
    tracks = get_tracks_by_type(ptype, input_str)
    existing = normalize(path.read_text().splitlines()) if path.exists() else []
    keys = {line.casefold() for line in existing}
    return dict(
        name=name.removesuffix(".txt"),
        tracks=tracks,
        existing=[line for line in tracks if line.casefold() in keys],
        new_tracks=[line for line in tracks if line.casefold() not in keys],
    )


def save_tracks(name, tracks, replace=False):
    path = source_path(name)
    tracks = normalize(tracks)
    existing_text = path.read_text(encoding="utf-8") if path.exists() and not replace else ""
    existing = normalize(existing_text.splitlines())
    keys = {line.casefold() for line in existing}
    new = [line for line in tracks if line.casefold() not in keys]
    if replace:
        content = "\n".join(tracks) + "\n"
    else:
        content = existing_text.rstrip() + "\n" if existing_text else ""
        if new:
            content += f"\n# Added {datetime.now(UTC).isoformat()}\n" + "\n".join(new) + "\n"
    atomic_write(path, content)
    return dict(
        name=name.removesuffix(".txt"),
        added=len(new),
        total=len(normalize(content.splitlines())),
        path=str(path),
        play_now_name=name.removesuffix(".txt"),
    )


def replace_source(name, content):
    """Replace an existing source verbatim, without parsing its contents."""
    path = source_path(name)
    if not path.is_file():
        raise FileNotFoundError("Playlist does not exist.")
    atomic_write(path, content)
    return dict(
        name=name.removesuffix(".txt"),
        path=str(path),
        play_now_name=name.removesuffix(".txt"),
    )


def create_playlist(ptype, input_str, name=None, dynamic=False):
    if dynamic:
        name = settings.get_setting("DYNAMIC_PLAYLIST_FILE")
    result = preview(ptype, input_str, name)
    saved = save_tracks(result["name"], result["tracks"], replace=dynamic)
    result["saved"] = saved
    result["play_now_name"] = saved["play_now_name"]
    if dynamic:
        result["published"] = write_m3u_files(result["name"])
    return result


def list_playlists(limit=50, offset=0):
    root = settings.current().directory("PLAYLIST_SOURCE_FOLDER")

    def sort_key(name):
        first = name[:1]
        group = 0 if first.isalpha() else 1 if first.isdigit() else 2
        natural = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r"(\d+)", name.casefold())
            if part
        )
        return group, natural

    names = sorted(
        (
            str(path.relative_to(root).with_suffix(""))
            for path in root.rglob("*.txt")
            if not any(part.startswith(".") for part in path.relative_to(root).parts)
            and path.resolve().is_relative_to(root.resolve())
        ),
        key=sort_key,
    )
    return dict(
        items=[{"name": name} for name in names[offset : offset + limit]],
        total=len(names),
        limit=limit,
        offset=offset,
    )


def write_m3u_files(single_file=None):
    source, _, _, m3u, missing, sorted_folder = map(Path, settings.get_apollo_folders())
    config = settings.current()
    published = (
        config.directory("PLAYLIST_PUBLISHED_FOLDER")
        if config.get("PLAYLIST_PUBLISHED_FOLDER")
        else None
    )
    if single_file:
        paths = [source_path(single_file)]
    else:
        paths = [source_path(row["name"]) for row in list_playlists(limit=10**9)["items"]]
    # Read all sources before writing; stale sorted output is never republished.
    sources = [(path, normalize(path.read_text(encoding="utf-8").splitlines())) for path in paths]
    es, index = estools.get_es()
    results = []
    for path, lines in sources:
        relative = str(path.relative_to(source))
        urls, tracks, duration, absent = estools.get_playlist_from_lines(es, index, lines)
        content = "#EXTM3U\n" + "\n".join(sorted(set(urls))) + "\n"
        output = str(Path(relative).with_suffix(".m3u"))
        atomic_write(contained(sorted_folder, relative), "\n".join(lines) + "\n")
        atomic_write(contained(m3u, output), content)
        atomic_write(contained(missing, relative), "\n".join(absent))
        if published:
            atomic_write(contained(published, output), content, mode=0o644)
        results.append(
            dict(
                name=str(Path(relative).with_suffix("")),
                count=len(set(urls)),
                duration=duration,
                missing=absent,
                output=str((published or m3u) / output),
            )
        )
    return dict(
        items=results,
        total=len(results),
        play_now_names=[result["name"] for result in results],
    )
