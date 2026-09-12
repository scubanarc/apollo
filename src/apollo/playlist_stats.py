"""Read-only statistics for a snapshot of playlist source text."""

import logging
import math
from collections import Counter

from apollo import estools
from apollo.playlist import TRACK_SEPARATOR

logger = logging.getLogger(__name__)


def calculate(content):
    songs, artists = {}, Counter()
    comments = blanks = 0
    invalid = []
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line:
            blanks += 1
            continue
        if line.startswith("#"):
            comments += 1
            continue
        parts = TRACK_SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            invalid.append(number)
            continue
        artist, title = (part.strip() for part in parts)
        key = (artist.casefold(), title.casefold())
        row = songs.setdefault(key, dict(artist=artist, title=title, count=0))
        row["count"] += 1
        artists[artist.casefold()] += 1

    count = sum(row["count"] for row in songs.values())
    result = dict(
        song_count=count,
        unique_songs=len(songs),
        duplicates=count - len(songs),
        unique_artists=len(artists),
        comment_lines=comments,
        blank_lines=blanks,
        invalid_lines=invalid,
        duplicate_songs=[
            dict(song=f"{r['artist']} - {r['title']}", count=r["count"])
            for r in songs.values()
            if r["count"] > 1
        ],
        top_artists=[
            dict(
                artist=next(r["artist"] for r in songs.values() if r["artist"].casefold() == key),
                count=n,
            )
            for key, n in artists.most_common(5)
        ],
        matched_songs=0,
        missing_songs=[],
        unchecked_songs=len(songs),
        duration_seconds=0,
        unique_duration_seconds=0,
        timed_entries=0,
        unknown_duration_songs=0,
        formats={},
        genres=[],
        genre_entries=0,
        unknown_genre_songs=0,
        warning=None,
        shortest=None,
        longest=None,
    )
    formats = Counter()
    genres = {}
    try:
        if not songs:
            return result
        es, index = estools.get_es()
        for position, row in enumerate(songs.values(), 1):
            best, _, _ = estools.pick_best_hit(
                estools.search_es(es, index, row["artist"], row["title"])
            )
            result["unchecked_songs"] -= 1
            label = f"{row['artist']} - {row['title']}"
            if best is None:
                result["missing_songs"].append(label)
                continue
            result["matched_songs"] += 1
            source = best["hit"]["_source"]
            formats[source.get("extension", "").lower().lstrip(".") or "unknown"] += 1
            raw_genres = source.get("genre")
            if not isinstance(raw_genres, list):
                raw_genres = [raw_genres]
            # Keep compound labels intact (e.g. Pop/Rock); merge case variants.
            labels = {}
            for genre in raw_genres:
                if isinstance(genre, str) and genre.strip():
                    genre_label = genre.strip()
                    labels.setdefault(genre_label.casefold(), genre_label)
            if labels:
                result["genre_entries"] += row["count"]
                for key, genre_label in labels.items():
                    genre = genres.setdefault(key, dict(genre=genre_label, songs=0, entries=0))
                    genre["songs"] += 1
                    genre["entries"] += row["count"]
            else:
                result["unknown_genre_songs"] += 1
            try:
                duration = float(source.get("duration") or 0)
            except (ValueError, TypeError):
                duration = 0
            if math.isfinite(duration) and duration > 0:
                result["duration_seconds"] += duration * row["count"]
                result["unique_duration_seconds"] += duration
                result["timed_entries"] += row["count"]
                item = dict(song=label, duration=duration)
                if result["shortest"] is None or duration < result["shortest"]["duration"]:
                    result["shortest"] = item
                if result["longest"] is None or duration > result["longest"]["duration"]:
                    result["longest"] = item
            else:
                result["unknown_duration_songs"] += 1
            if position % 25 == 0:
                logger.info("Checked %s of %s unique songs", position, len(songs))
    except Exception:
        logger.info("Library lookup unavailable; returning available playlist statistics.")
        result["warning"] = "Library lookup unavailable. Library statistics are incomplete."
    result["formats"] = dict(formats)
    result["genres"] = sorted(
        genres.values(), key=lambda genre: (-genre["entries"], genre["genre"].casefold())
    )
    return result
