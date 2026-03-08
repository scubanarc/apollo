import hashlib
import json
import os
import re
import secrets
from collections import Counter
from datetime import datetime

import requests

from apollo_lib import estools, ratings, settings


def _normalize_relative_path(filename):
    """Return a Navidrome-style relative path (forward slashes)."""
    music_root = os.path.abspath(settings.get_setting("MUSIC_FOLDER"))
    input_path = os.path.abspath(filename) if os.path.isabs(filename) else filename

    if os.path.isabs(input_path):
        try:
            rel = os.path.relpath(input_path, music_root)
        except ValueError:
            rel = os.path.basename(input_path)
    else:
        rel = input_path

    rel = rel.replace("\\", "/").lstrip("/")
    return rel


def _get_client_settings():
    """Load and normalize Navidrome connection settings."""
    base_url = settings.get_setting("NAVIDROME_URL").rstrip("/")
    username = settings.get_setting("NAVIDROME_UN")
    password = settings.get_setting("NAVIDROME_PWD")
    return base_url, username, password


def _auth_params():
    """Build Subsonic token auth query params used by Navidrome."""
    _, username, password = _get_client_settings()
    salt = secrets.token_hex(6)
    token = hashlib.md5((password + salt).encode("utf-8")).hexdigest()
    return {
        "u": username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "apollo",
        "f": "json",
    }


def _subsonic_get(endpoint, params=None, timeout=15):
    """Call a Subsonic endpoint and return the parsed response object."""
    base_url, _, _ = _get_client_settings()
    call_params = _auth_params()
    if params:
        call_params.update(params)

    url = f"{base_url}/rest/{endpoint}.view"
    response = requests.get(url, params=call_params, timeout=timeout)
    response.raise_for_status()

    payload = response.json()
    sub = payload.get("subsonic-response", {})
    if sub.get("status") != "ok":
        error = sub.get("error", {})
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown Subsonic API error")
        raise RuntimeError(f"Navidrome API error ({code}): {message}")
    return sub


def _pick_song_by_filename(songs, filename):
    """Pick the best song candidate matching a relative file path."""
    rel = _normalize_relative_path(filename)
    rel_cf = rel.casefold()

    exact = None
    endswith_match = None
    basename = os.path.basename(rel_cf)
    basename_match = None

    for song in songs or []:
        song_path = (song.get("path") or "").replace("\\", "/").lstrip("/")
        song_cf = song_path.casefold()
        if not song_cf:
            continue

        if song_cf == rel_cf:
            exact = song
            break
        if song_cf.endswith("/" + rel_cf) or rel_cf.endswith("/" + song_cf):
            endswith_match = song
        if os.path.basename(song_cf) == basename:
            basename_match = song

    if exact:
        return exact
    if endswith_match:
        return endswith_match
    return basename_match


def _normalize_text(value):
    """Normalize text for loose artist/title matching."""
    if value is None:
        return ""

    text = str(value)
    replacements = {
        "’": "'",
        "‘": "'",
        "`": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_song_by_filename(filename):
    """Find a Navidrome song object by filename/path."""
    rel = _normalize_relative_path(filename)
    stem = os.path.splitext(os.path.basename(rel))[0]
    cleaned_stem = re.sub(r"^\d+(\s*-\s*\d+)?\s*-\s*", "", stem).strip()
    queries = [stem]
    if cleaned_stem and cleaned_stem != stem:
        queries.append(cleaned_stem)

    for query in queries:
        sub = _subsonic_get("search3", {"query": query, "songCount": 100, "songOffset": 0})
        search_result = sub.get("searchResult3", {})
        songs = search_result.get("song", [])

        if isinstance(songs, dict):
            songs = [songs]

        song = _pick_song_by_filename(songs, rel)
        if song:
            return song

    raise LookupError(f"Song not found in Navidrome for filename: {rel}")


def find_song_by_artist_title(artist, title):
    """Find a Navidrome song object by artist and title."""
    raw_query = f"{artist} {title}".strip()
    normalized_query = f"{_normalize_text(artist)} {_normalize_text(title)}".strip()
    query_candidates = [raw_query]
    if normalized_query and normalized_query != raw_query:
        query_candidates.append(normalized_query)

    songs = []
    for query in query_candidates:
        sub = _subsonic_get("search3", {"query": query, "songCount": 100, "songOffset": 0})
        search_result = sub.get("searchResult3", {})
        batch = search_result.get("song", [])
        if isinstance(batch, dict):
            batch = [batch]
        songs.extend(batch)

    artist_norm = _normalize_text(artist)
    title_norm = _normalize_text(title)

    for song in songs:
        s_artist = _normalize_text(song.get("artist"))
        s_title = _normalize_text(song.get("title"))
        if s_artist == artist_norm and s_title == title_norm:
            return song

    for song in songs:
        s_artist = _normalize_text(song.get("artist"))
        s_title = _normalize_text(song.get("title"))
        if artist_norm in s_artist and title_norm in s_title:
            return song

    raise LookupError(f"Song not found in Navidrome for: {artist} - {title}")


def set_rating_by_song_id(song_id, rating):
    """Set a song rating in Navidrome by song id."""
    rating_int = int(round(float(rating)))
    if rating_int < 1 or rating_int > 5:
        raise ValueError("Navidrome rating must be between 1 and 5")

    _subsonic_get("setRating", {"id": song_id, "rating": rating_int})
    return {"id": song_id, "rating": rating_int}


def get_rating_by_song_id(song_id):
    """Read a song rating from Navidrome by song id."""
    sub = _subsonic_get("getSong", {"id": song_id})
    song_payload = sub.get("song", {})
    return song_payload.get("userRating")


def set_rating_by_filename(filename, rating):
    """Set a song rating in Navidrome by filename.

    Rating must be an integer between 1 and 5.
    """
    rating_int = int(round(float(rating)))
    if rating_int < 1 or rating_int > 5:
        raise ValueError("Navidrome rating must be between 1 and 5")

    song = find_song_by_filename(filename)
    song_id = song.get("id")
    if not song_id:
        raise LookupError(f"Navidrome song has no id for filename: {filename}")

    result = set_rating_by_song_id(song_id, rating_int)
    result["path"] = song.get("path")
    return result


def get_rating_by_filename(filename):
    """Read a song rating from Navidrome by filename."""
    song = find_song_by_filename(filename)
    song_id = song.get("id")
    if not song_id:
        raise LookupError(f"Navidrome song has no id for filename: {filename}")
    return get_rating_by_song_id(song_id)


def _normalize_user_rating(value):
    """Normalize Navidrome userRating into int 1..5 when possible."""
    try:
        normalized = int(round(float(value)))
    except (TypeError, ValueError):
        return None

    if normalized < 1 or normalized > 5:
        return None
    return normalized


def _get_fail_log_path():
    """Return the log path for Navidrome sync failures."""
    _, apollo_folder, _, _, _, _ = settings.get_apollo_folders()
    return os.path.join(apollo_folder, "navidrome-sync-failures.log")


def _write_fail_log(failed_items):
    """Write failed sync entries to a JSONL log file."""
    if not failed_items:
        return None

    log_path = _get_fail_log_path()
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "failure_count": len(failed_items),
        }, ensure_ascii=False) + "\n")

        for item in failed_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    return log_path


def apollo_rating_to_navidrome(calculated_rating):
    """Map Apollo calculated rating (roughly 0-100) to Navidrome's 1-5 scale."""
    try:
        value = float(calculated_rating)
    except (TypeError, ValueError):
        value = 50.0

    value = max(0.0, min(100.0, value))
    mapped = 1.0 + (value / 100.0) * 4.0
    return int(round(mapped))


def _get_best_filename_for_song(es, index_name, artist, title):
    """Resolve the best local file path for an artist/title via Elasticsearch."""
    result = estools.search_es(es, index_name, artist, title)
    if not result or result.get("hits", {}).get("total", {}).get("value", 0) == 0:
        return None

    best, _, _ = estools.pick_best_hit(result)
    if not best:
        return None

    source = best.get("hit", {}).get("_source", {})
    url = source.get("url")
    if not url:
        return None
    return url


def update_all_ratings(verbose=False):
    """Push all ratings from apollo_calculated_rating to Navidrome by filename."""
    dbh, sth = ratings.get_db_connection()
    if not dbh or not sth:
        return {
            "total": 0,
            "updated": 0,
            "missing_file": 0,
            "unchanged": 0,
            "failed": 0,
        }

    sth.execute("SELECT artist, title, calculated_rating FROM apollo_calculated_rating")
    rows = sth.fetchall() or []

    total = len(rows)
    if total == 0:
        print("No rows found in apollo_calculated_rating.")
        return {
            "total": 0,
            "updated": 0,
            "missing_file": 0,
            "unchanged": 0,
            "failed": 0,
        }

    # Fail fast if Navidrome is unreachable or auth is invalid.
    _subsonic_get("ping", timeout=5)

    es, index_name = estools.get_es()

    updated = 0
    missing_file = 0
    unchanged = 0
    failed = 0
    processed = 0
    failed_items = []

    print(f"Starting Navidrome rating sync for {total} songs...")

    for row in rows:
        processed += 1
        artist = row.get("artist")
        title = row.get("title")
        calculated = row.get("calculated_rating")

        nd_rating = apollo_rating_to_navidrome(calculated)

        try:
            song = find_song_by_artist_title(artist, title)
            song_id = song.get("id")
            if not song_id:
                raise LookupError(f"Navidrome song has no id for: {artist} - {title}")
            current_rating = _normalize_user_rating(song.get("userRating"))
            if current_rating is None:
                current_rating = _normalize_user_rating(get_rating_by_song_id(song_id))

            if current_rating == nd_rating:
                unchanged += 1
                if verbose:
                    print(f"Unchanged rating {nd_rating}: {artist} - {title}")
            else:
                set_rating_by_song_id(song_id, nd_rating)
                updated += 1
                if verbose:
                    print(f"Updated rating {nd_rating}: {artist} - {title}")
        except Exception as exc:
            # Fallback to filename-based lookup when artist/title lookup fails.
            filename = _get_best_filename_for_song(es, index_name, artist, title)
            if not filename:
                missing_file += 1
                failed += 1
                failed_items.append({
                    "artist": artist,
                    "title": title,
                    "target_rating": nd_rating,
                    "artist_title_error": str(exc),
                    "filename": None,
                    "fallback_error": "No ES filename match",
                })
                if verbose:
                    print(f"Missing filename in ES for: {artist} - {title}")
            else:
                try:
                    song = find_song_by_filename(filename)
                    song_id = song.get("id")
                    if not song_id:
                        raise LookupError(f"Navidrome song has no id for filename: {filename}")

                    current_rating = _normalize_user_rating(song.get("userRating"))
                    if current_rating is None:
                        current_rating = _normalize_user_rating(get_rating_by_song_id(song_id))

                    if current_rating == nd_rating:
                        unchanged += 1
                        if verbose:
                            print(f"Unchanged rating {nd_rating}: {artist} - {title} ({filename})")
                    else:
                        set_rating_by_song_id(song_id, nd_rating)
                        updated += 1
                        if verbose:
                            print(f"Updated rating {nd_rating}: {artist} - {title} ({filename})")
                except Exception as fallback_exc:
                    failed += 1
                    failed_items.append({
                        "artist": artist,
                        "title": title,
                        "target_rating": nd_rating,
                        "artist_title_error": str(exc),
                        "filename": filename,
                        "fallback_error": str(fallback_exc),
                    })
                    if verbose:
                        print(f"Failed to update {artist} - {title}: {fallback_exc}")
                    elif processed <= 3:
                        print(f"Sample failure {artist} - {title}: {fallback_exc}")

        if not verbose and (processed % 100 == 0 or processed == total):
            print(
                f"Progress {processed}/{total} | "
                f"updated={updated} unchanged={unchanged} "
                f"missing_file={missing_file} failed={failed}"
            )

    fail_log_path = _write_fail_log(failed_items)
    reason_counter = Counter()
    for item in failed_items:
        reason = item.get("fallback_error") or item.get("artist_title_error") or "Unknown"
        reason_counter[reason] += 1

    if failed_items:
        print(f"Failure log written: {fail_log_path}")
        top_reasons = reason_counter.most_common(5)
        if top_reasons:
            print("Top failure reasons:")
            for reason, count in top_reasons:
                print(f"  {count}x {reason}")

    return {
        "total": total,
        "updated": updated,
        "missing_file": missing_file,
        "unchanged": unchanged,
        "failed": failed,
        "fail_log": fail_log_path,
    }