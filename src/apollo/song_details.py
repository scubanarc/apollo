"""Read-only explanation of library matches and the current playback choice."""

from elasticsearch.helpers import scan

from apollo import estools, playlist, ratings, settings


def details(song):
    if not isinstance(song, str) or len(song) > 2000:
        raise ValueError("Provide a song in artist - title form.")
    tracks = playlist.normalize([song])
    if len(tracks) != 1:
        raise ValueError("Provide a song in artist - title form.")
    artist, title = tracks[0].split(" - ", 1)
    es, index = estools.get_es()
    result = estools.search_es(es, index, artist, title)
    best, candidates, _ = estools.pick_best_hit(result)
    patterns = estools.load_patterns(settings.current().path.parent / "priority.yml")
    selected_id = best["hit"]["_id"] if best else None
    eligible = {c["hit"]["_id"] for c in candidates}
    playback_ids = {h["_id"] for h in result["hits"]["hits"]}
    entries = []
    for hit in scan(es, index=index, query=estools.song_query(artist, title), preserve_order=True):
        source = hit["_source"]
        factors = estools.describe_hit(hit, patterns)
        extension = str(source.get("extension") or "").lower()
        normalized = (
            float(source.get("bitrate") or 0)
            if extension == ".flac"
            else estools.get_normalized_bitrate(source.get("bitrate"), extension)
        )
        winner = hit["_id"] == selected_id
        if winner:
            reason = "Winner: highest search score, then FLAC, bitrate, and pattern priority; ties keep the first result."
        elif hit["_id"] not in playback_ids:
            reason = "Outside the first 10 search results considered by current playback."
        elif hit["_id"] not in eligible:
            reason = "Lower search relevance than the winner."
        else:
            winning = best["hit"]["_source"]
            winning_ext = str(winning.get("extension") or "").lower()
            winning_rate = (
                float(winning.get("bitrate") or 0)
                if winning_ext == ".flac"
                else estools.get_normalized_bitrate(winning.get("bitrate"), winning_ext)
            )
            if winning_ext == ".flac" and extension != ".flac":
                reason = "FLAC is preferred over this format."
            elif normalized < winning_rate:
                reason = "Lower effective bitrate than the winner."
            elif factors["priority"] < best["priority"]:
                reason = "Lower pattern priority than the winner."
            else:
                reason = "Tied on all selection factors; the first search result wins."
        entries.append(
            dict(
                id=hit["_id"],
                source=source,
                score=hit.get("_score"),
                priority=factors["priority"],
                patterns=factors["patterns"],
                effective_bitrate=normalized,
                winner=winner,
                reason=reason,
            )
        )
    entries.sort(key=lambda entry: not entry["winner"])
    summary, warning = None, None
    try:
        summary = ratings.calculate_rating(artist, title)
    except Exception:
        warning = "Rating summary unavailable. Check MySQL configuration and connectivity."
    threshold = settings.get_setting("RATING_THRESHOLD", 45)
    return dict(
        song=tracks[0],
        entries=entries,
        total=len(entries),
        rating=summary,
        rating_warning=warning,
        threshold=threshold,
        excluded=summary is not None and summary["calculated_rating"] < threshold,
        winner_id=selected_id,
    )
