"""Read-only comparison using the same metadata and quality rules as scanning."""

from apollo import estools
from apollo.scanner import audio_files, metadata


def compare_directory(directory):
    files = audio_files(directory)
    es, index = estools.get_es()
    items, errors = [], []
    for path in files:
        try:
            doc = metadata(path)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        best, _, _ = estools.pick_best_hit(
            estools.search_es(es, index, doc["artist"], doc["title"])
        )
        old = best["hit"]["_source"] if best else None
        reason = None
        if old is None:
            reason = "New song"
        else:
            new_flac, old_flac = (
                doc["extension"] == ".flac",
                old.get("extension", "").lower() == ".flac",
            )
            if new_flac and not old_flac:
                reason = "FLAC preferred"
            elif new_flac == old_flac and estools.get_normalized_bitrate(
                doc["bitrate"], doc["extension"]
            ) > estools.get_normalized_bitrate(old.get("bitrate", 0), old.get("extension", "")):
                reason = "Higher bitrate" if new_flac else "Higher normalized bitrate"
        if reason:
            items.append(
                dict(
                    doc,
                    reason=reason,
                    old_file=old.get("url") if old else None,
                    old_bitrate=old.get("bitrate") if old else None,
                )
            )
    return dict(items=items, total=len(items), examined=len(files), errors=errors)
