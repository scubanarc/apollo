import yaml
import re
from elasticsearch import Elasticsearch
from colorama import Fore, Style
from apollo_lib import settings
from apollo_lib import ratings
from platformdirs import user_config_dir

# A collection of utility functions for interacting with Elasticsearch

# Global variable to cache patterns loaded from YAML
_pattern_cache = None

def get_es():
    """Get the Elasticsearch client and index name from settings."""
    es_index = settings.get_setting('ES_INDEX')
    es_url = settings.get_setting('ES_URL')
    es = Elasticsearch(es_url)
    return es, es_index

def print_hit(hit):
    """Print the details of a single Elasticsearch hit."""
    src = hit.get('_source', {})
    print(Fore.YELLOW + f"Album Artist: {src.get('albumartist', '')}")
    print(Fore.RESET + f"  Artist: {src.get('artist', '')}")
    print(Fore.RESET + f"  Title: {src.get('title', '')}")
    print(f"  Url: {src.get('url', '')}")
    print(f"  Album: {src.get('album', '')}")
    print(f"  Year: {src.get('year', '')}")
    print(f"  Genre: {src.get('genre', '')}")
    print(f"  Bitrate: {src.get('bitrate', '')}")
    print(f"  Samplerate: {src.get('samplerate', '')}")
    print(f"  Duration: {src.get('duration', '')}")
    print(f"  Size: {src.get('size', '')}")
    print(f"  Mod Time: {src.get('modification_time', '')}")
    print(f"  VBR: {src.get('vbr', '')}")
    print(f"  Extension: {src.get('extension', '')}")
    print(f"  Match score: {hit.get('_score', '')}")

    if 'priority' in hit:
        print(f"  Priority: {hit.get('priority', '')}")
    if 'patterns' in hit:
        print(f"  Patterns: {hit.get('patterns', [])}")

    print(Style.RESET_ALL)

def get_normalized_bitrate(bitrate, extension):
    """Calculate normalized bitrate based on format-specific multipliers."""
    extension = extension.lower()
    
    # Get bitrate multipliers from settings
    try:
        multipliers = settings.get_setting("BITRATE_MULTIPLIERS")
        multiplier = multipliers.get(extension, 1.0)
        return bitrate * multiplier
    except:
        # Fallback to default multipliers if setting not found
        default_multipliers = {
            ".mp3": 1.0,
            ".ogg": 1.3,
            ".m4a": 1.2,
            ".aac": 1.2,
            ".mp4": 1.2,
            ".flac": 1.0
        }
        multiplier = default_multipliers.get(extension, 1.0)
        return bitrate * multiplier

def load_patterns(file_path):
    """Load patterns from a YAML file."""
    global _pattern_cache
    if _pattern_cache is None:
        with open(file_path, 'r') as file:
            _pattern_cache = yaml.safe_load(file).get("patterns", [])
    return _pattern_cache

def pick_best_hit(result, patterns_path=None):
    """Pick the best hit from Elasticsearch results based on score and patterns."""
    import os
    if patterns_path is None:
        CONFIG_DIR = user_config_dir("apollo")
        patterns_path = os.path.join(CONFIG_DIR, 'priority.yml')
    patterns = load_patterns(patterns_path)

    max_score = 0
    candidates = []
    for hit in result["hits"]["hits"]:
        if hit["_score"] > max_score:
            max_score = hit["_score"]
    inner_debug_string = f"Max score: {max_score}\n"

    for hit in result["hits"]["hits"]:
        if hit["_score"] == max_score:
            priority = 100
            matched_patterns = []
            for pattern in patterns:
                regex = re.compile(pattern.get("pattern", ""), re.IGNORECASE)
                for field in pattern.get("applies_to", []):
                    if regex.search(hit["_source"].get(field, "")):
                        priority += pattern.get("weight", 0)
                        matched_patterns.append(pattern.get("pattern", ""))
            candidates.append({
                "hit": hit,
                "priority": priority,
                "patterns": matched_patterns,
            })

    # First, separate FLAC files from others
    flac_candidates = []
    other_candidates = []
    
    for c in candidates:
        extension = c["hit"]["_source"].get("extension", "").lower()
        if extension == ".flac":
            flac_candidates.append(c)
        else:
            other_candidates.append(c)
    
    inner_debug_string += f"FLAC candidates: {len(flac_candidates)}, Other candidates: {len(other_candidates)}\n"
    
    best = None
    
    # If we have FLAC candidates, prioritize them
    if flac_candidates:
        inner_debug_string += "Using FLAC priority logic\n"
        max_bitrate = 0
        for c in flac_candidates:
            bitrate = c["hit"]["_source"].get("bitrate", 0)
            if bitrate >= max_bitrate:
                if bitrate == max_bitrate:
                    if best is None or c["priority"] > best["priority"]:
                        inner_debug_string += f"FLAC priority: {c['priority']} > {best['priority'] if best else 'None'}\n"
                        best = c
                else:
                    inner_debug_string += f"New FLAC max bitrate: {bitrate} > {max_bitrate}\n"
                    max_bitrate = bitrate
                    best = c
        inner_debug_string += f"Selected FLAC with bitrate: {max_bitrate}\n"
    else:
        # No FLAC files, use normalized bitrate comparison
        inner_debug_string += "Using normalized bitrate comparison\n"
        max_normalized_bitrate = 0
        for c in other_candidates:
            hit_source = c["hit"]["_source"]
            bitrate = hit_source.get("bitrate", 0)
            extension = hit_source.get("extension", "")
            normalized_bitrate = get_normalized_bitrate(bitrate, extension)
            
            inner_debug_string += f"File {extension}: actual={bitrate}, normalized={normalized_bitrate:.1f}\n"
            
            if normalized_bitrate >= max_normalized_bitrate:
                if normalized_bitrate == max_normalized_bitrate:
                    if best is None or c["priority"] > best["priority"]:
                        inner_debug_string += f"Normalized priority: {c['priority']} > {best['priority'] if best else 'None'}\n"
                        best = c
                else:
                    inner_debug_string += f"New max normalized bitrate: {normalized_bitrate:.1f} > {max_normalized_bitrate:.1f}\n"
                    max_normalized_bitrate = normalized_bitrate
                    best = c
        inner_debug_string += f"Selected with normalized bitrate: {max_normalized_bitrate:.1f}\n"
    return best, candidates, inner_debug_string

def get_playlist_from_lines(es, index_name, lines):
    """Get a playlist from a list of lines, searching for each line in Elasticsearch."""
    urls = []
    tracks = []
    missing = []
    duration = 0
    for raw in lines:
        song = raw.strip()

        if not song or song.startswith('#'):
            continue
        
        song = re.sub(r"\s*-\s*", " - ", song, count=1)
        if " - " not in song:
            continue

        artist, title = [part.strip() for part in song.split(" - ", 1)]
        if not artist or not title:
            continue
        
        calculated_rating = ratings.get_calculated_rating(artist, title)
        
        rating_threshold = settings.get_setting('RATING_THRESHOLD', 45)
        if calculated_rating is not None:
            if calculated_rating < rating_threshold:
                print(f"{Fore.RED}Low calculated rating for {artist} - {title}: {calculated_rating}")
                continue
        
        result = search_es(es, index_name, artist, title)
        if not result or "hits" not in result or "total" not in result["hits"]:
            continue

        if result["hits"]["total"].get("value", 0) == 0:
            print(f"{Fore.RED}No results found for {raw}")
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
    return urls, tracks, duration, missing

def search_es(es, index_name, artist, title):
    """Search for a song in Elasticsearch by artist and title."""
    query_body = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"artist": artist}},
                    {"match": {"title": title}}
                ],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}}
                ],
            },
        },
        "size": 10,
    }
    result = es.search(index=index_name, body=query_body)
    return result

def get_all_by_artist(es, index_name, artist):
    """Get all songs by a specific artist from Elasticsearch."""
    query_body = {
        "query": {
            "bool": {
                "must": [
                    {"match_phrase_prefix": {"artist": artist}},
                ],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}}
                ],
            },
        },
        "size": 500,
    }

    result = es.search(index=index_name, body=query_body)

    lines = []
    for hit in result["hits"]["hits"]:
        lines.append(f"{hit['_source']['artist']} - {hit['_source']['title']}")

    lines = list(set(lines))
    return lines

def get_all_by_path(es, index_name, path):
    """Get all songs in a specific path from Elasticsearch."""
    query_body = {
        "query": {
            "bool": {
                "must": [
                    {"match_phrase_prefix": {"url": path}},
                ],
                "should": [
                    {"range": {"bitrate": {"gte": 320}}},
                    {"range": {"samplerate": {"gte": 48000}}}
                ],
            },
        },
        "size": 500,
    }

    result = es.search(index=index_name, body=query_body)

    lines = []
    for hit in result["hits"]["hits"]:
        lines.append(f"{hit['_source']['artist']} - {hit['_source']['title']}")

    lines = list(set(lines))
    return lines
