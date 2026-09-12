import logging
import re

import yaml
from elasticsearch import Elasticsearch

from apollo import ratings, settings

logger = logging.getLogger(__name__)


def get_es():
    from apollo.runtime import operation

    state = operation()
    if "elasticsearch" not in state.cache:
        es = Elasticsearch(settings.get_setting("ES_URL"), request_timeout=15, max_retries=1)
        state.stack.callback(es.close)
        state.cache["elasticsearch"] = es
    return (state.cache["elasticsearch"], settings.get_setting("ES_INDEX"))


def get_normalized_bitrate(bitrate, extension):
    multipliers = settings.get_setting("BITRATE_MULTIPLIERS")
    return float(bitrate or 0) * multipliers.get(extension.lower(), 1.0)


def load_patterns(file_path):
    from pathlib import Path

    from apollo.runtime import operation

    cache = operation().cache
    key = "patterns:" + str(file_path)
    if key not in cache:
        path = Path(file_path)
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        cache[key] = (data or {}).get("patterns", [])
    return cache[key]


def describe_hit(hit, patterns):
    priority, matched = 100, []
    for pattern in patterns:
        regex = re.compile(pattern["pattern"], re.IGNORECASE)
        for field in pattern.get("applies_to", []):
            if regex.search(str(hit["_source"].get(field) or "")):
                priority += pattern.get("weight", 0)
                matched.append(pattern["pattern"])
    return dict(hit=hit, priority=priority, patterns=matched)


def pick_best_hit(result, patterns_path=None):
    """Choose top search score, then FLAC, bitrate and pattern priority."""
    patterns = load_patterns(patterns_path or settings.current().path.parent / "priority.yml")
    hits = result["hits"]["hits"]
    if not hits:
        return None, [], "No matches"
    score = max(hit.get("_score") or 0 for hit in hits)
    candidates = []
    for hit in hits:
        if (hit.get("_score") or 0) != score:
            continue
        candidates.append(describe_hit(hit, patterns))

    def quality(candidate):
        source = candidate["hit"]["_source"]
        extension = source.get("extension", "").lower()
        bitrate = float(source.get("bitrate") or 0)
        return (
            extension == ".flac",
            bitrate if extension == ".flac" else get_normalized_bitrate(bitrate, extension),
            candidate["priority"],
        )

    best = max(candidates, key=quality)
    return (
        best,
        candidates,
        f"Selected quality {quality(best)} among {len(candidates)} top-score matches",
    )


def get_playlist_from_lines(es, index_name, lines):
    """Get a playlist from a list of lines, searching for each line in Elasticsearch."""
    urls = []
    tracks = []
    missing = []
    duration = 0
    for raw in lines:
        song = raw.strip()
        if not song or song.startswith("#"):
            continue
        if " - " not in song:
            continue
        artist, title = [part.strip() for part in song.split(" - ", 1)]
        if not artist or not title:
            continue
        calculated_rating = ratings.get_calculated_rating(artist, title)
        rating_threshold = settings.get_setting("RATING_THRESHOLD", 45)
        if calculated_rating is not None:
            if calculated_rating < rating_threshold:
                logger.info(f"Low calculated rating for {artist} - {title}: {calculated_rating}")
                continue
        result = search_es(es, index_name, artist, title)
        if not result or "hits" not in result:
            raise ValueError("Elasticsearch returned an invalid search result.")
        if not result["hits"]["hits"]:
            logger.info(f"No results found for {raw}")
            missing.append(raw)
            continue
        best, candidates, debug_info = pick_best_hit(result)
        es_title = best["hit"]["_source"].get("title", "")
        es_artist = best["hit"]["_source"].get("artist", "")
        es_url = best["hit"]["_source"].get("url", "")
        urls.append(f"{es_url}")
        track = es_artist + " - " + es_title
        tracks.append(track)
        duration += best["hit"]["_source"].get("duration", 0)
    return (urls, tracks, duration, missing)


def song_query(artist, title):
    """Shared matching query for playback and song inspection."""
    query_body = {
        "query": {
            "bool": {
                "must": [{"match": {"artist": artist}}, {"match": {"title": title}}],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}},
                ],
            }
        },
        "size": 10,
    }
    return query_body


def search_es(es, index_name, artist, title):
    """Search for a song in Elasticsearch by artist and title."""
    return es.search(index=index_name, body=song_query(artist, title))


def get_all_by_artist(es, index_name, artist):
    """Get all songs by a specific artist from Elasticsearch."""
    query_body = {
        "query": {
            "bool": {
                "must": [{"match_phrase_prefix": {"artist": artist}}],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}},
                ],
            }
        },
        "size": 500,
    }
    from elasticsearch.helpers import scan

    lines = []
    for hit in scan(es, index=index_name, query={"query": query_body["query"]}):
        lines.append(f"{hit['_source']['artist']} - {hit['_source']['title']}")
    lines = list(set(lines))
    return lines


def get_all_by_path(es, index_name, path):
    """Get all songs in a specific path from Elasticsearch."""
    query_body = {
        "query": {
            "bool": {
                "must": [{"match_phrase_prefix": {"url": path}}],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}},
                ],
            }
        },
        "size": 2000,
    }
    from elasticsearch.helpers import scan

    lines = []
    for hit in scan(es, index=index_name, query={"query": query_body["query"]}):
        lines.append(f"{hit['_source']['artist']} - {hit['_source']['title']}")
    lines = list(set(lines))
    return lines
