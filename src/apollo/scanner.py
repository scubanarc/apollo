"""Audio metadata indexing with conservative, explicitly requested pruning."""

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from elasticsearch.helpers import scan
from mutagen import File as MutagenFile

from apollo import estools, settings
from apollo.files import atomic_write

logger = logging.getLogger(__name__)


def get_tag_value(audiofile, tag_keys):
    for key in tag_keys:
        value = (audiofile.tags or {}).get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value)
    return None


def ffprobe_bitrate(file_path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "json",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    info = json.loads(result.stdout)["format"]
    return float(info["duration"]), int(info["bit_rate"])


def audio_files(directory):
    directory = Path(directory).expanduser().absolute()
    if not directory.is_dir():
        raise ValueError(f"Music directory is unavailable: {directory}")

    def fail(error):
        raise error

    extensions = tuple(settings.get_setting("SUPPORTED_EXTENSIONS"))
    # Complete enumeration before any indexing; unreadable subdirectories abort the run.
    return sorted(
        Path(root) / name
        for root, _, names in os.walk(directory, onerror=fail)
        for name in names
        if name.lower().endswith(extensions)
    )


def metadata(path):
    audio = MutagenFile(path)
    if audio is None:
        raise ValueError("Audio metadata could not be read.")
    tags = {
        "title": ["TIT2", "TITLE", "©nam"],
        "artist": ["TPE1", "ARTIST", "©ART"],
        "album": ["TALB", "ALBUM", "©alb"],
        "albumartist": ["TPE2", "ALBUMARTIST", "aART"],
        "genre": ["TCON", "GENRE", "©gen"],
    }
    doc = {name: get_tag_value(audio, keys) for name, keys in tags.items()}
    if not doc["artist"] or not doc["title"]:
        raise ValueError("Artist or title tag is missing.")
    year = get_tag_value(audio, ["TDRC", "DATE", "©day", "YEAR"]) or ""
    match = re.search(r"\d{4}", year)
    info = audio.info
    stat = path.stat()
    duration = getattr(info, "length", 0)
    bitrate = getattr(info, "bitrate", 0) or getattr(info, "bitrate_nominal", 0)
    if (not bitrate or bitrate == 32000) and duration > 0:
        bitrate = round(stat.st_size * 8 / duration)
        if path.suffix.lower() == ".mp3" and bitrate == 32000:
            duration, bitrate = ffprobe_bitrate(path)
    doc.update(
        year=int(match[0]) if match else 0,
        url=str(path),
        extension=path.suffix.lower(),
        duration=duration,
        bitrate=bitrate,
        samplerate=getattr(info, "sample_rate", 0),
        size=stat.st_size,
        modification_time=stat.st_mtime,
        vbr=bool(getattr(info, "bitrate_mode", 0)),
    )
    return doc


def scan_music_folder_into_es(prune=False):
    directory = settings.current().directory("MUSIC_FOLDER")
    files = audio_files(directory)
    if not files:
        raise ValueError(
            "No supported music files found. Check the music mount; index was not changed."
        )
    _, _, ai, *_ = settings.get_apollo_folders()
    es, index = estools.get_es()
    existing = {}
    index_exists = bool(es.indices.exists(index=index))
    if index_exists:
        existing = {
            hit["_id"]: hit["_source"]
            for hit in scan(es, index=index, query={"query": {"match_all": {}}})
        }
    errors = []
    updated = 0
    unchanged = 0
    for number, path in enumerate(files, 1):
        try:
            stat = path.stat()
            old = existing.get(str(path), {})
            if (
                old.get("size") == stat.st_size
                and old.get("modification_time") == stat.st_mtime
                and old.get("bitrate") not in (None, 0, 32000)
                and old.get("artist")
                and old.get("title")
            ):
                unchanged += 1
                if number % 100 == 0 or number == len(files):
                    logger.info("Scanned %s/%s files (%s unchanged)", number, len(files), unchanged)
                continue
            doc = metadata(path)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            logger.warning("Could not read %s: %s", path, exc)
            continue
        es.update(index=index, id=str(path), doc=doc, doc_as_upsert=True)
        updated += 1
        if number % 100 == 0 or number == len(files):
            logger.info("Scanned %s/%s files", number, len(files))
    if not index_exists and not updated:
        return dict(
            total=len(files),
            updated=0,
            unchanged=0,
            deleted=0,
            errors=errors,
            prune_skipped=bool(prune),
        )
    # Refresh before exporting so the JSONL reflects this run's updates.
    es.indices.refresh(index=index)
    deleted = 0
    if prune and errors:
        logger.warning("Pruning skipped because some files could not be read.")
    elif prune:
        deleted = prune_missing_files_from_es(directory, {str(p) for p in files}, es, index)
    rows = [
        dict(hit["_source"], id=hit["_id"])
        for hit in scan(es, index=index, query={"query": {"match_all": {}}})
    ]
    atomic_write(
        Path(ai) / "es.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    return dict(
        total=len(files),
        updated=updated,
        unchanged=unchanged,
        deleted=deleted,
        errors=errors,
        prune_skipped=bool(prune and errors),
    )


def prune_missing_files_from_es(input_directory, scanned_files, es, es_index):
    directory = Path(input_directory).absolute()
    if not directory.is_dir() or not scanned_files:
        raise ValueError("Refusing to prune an unavailable or empty music library.")
    # A changed mount or interrupted traversal must not delete index entries.
    if {str(p) for p in audio_files(directory)} != scanned_files:
        raise ValueError("Music library changed during scanning; pruning aborted.")
    missing = []
    for hit in scan(es, index=es_index, query={"query": {"match_all": {}}}):
        path = Path(hit["_id"])
        if path.is_absolute() and path.is_relative_to(directory) and str(path) not in scanned_files:
            if not path.exists():
                missing.append(str(path))
    # Recheck after the remote index read, immediately before deleting anything.
    if {str(p) for p in audio_files(directory)} != scanned_files:
        raise ValueError("Music library changed during pruning; index entries retained.")
    for filename in missing:
        es.delete(index=es_index, id=filename)
    if missing:
        es.indices.refresh(index=es_index)
    return len(missing)
