import os
from typing import List, Dict, Any
from mutagen import File as MutagenFile
from colorama import Fore, Style
from apollo_lib import estools
from apollo_lib import settings
import re


def get_tag_value(audiofile, tag_keys):
    """Get tag value using multiple possible keys for different formats."""
    for key in tag_keys:
        try:
            if key in audiofile.tags:
                value = audiofile.tags[key]
                if isinstance(value, list) and value:
                    return str(value[0])
                elif value:
                    return str(value)
        except (KeyError, AttributeError, TypeError):
            continue
    return None

def compare_directory(dir_to_scan: str) -> None:
    """
    Compare the contents of a directory against the Elasticsearch index
    Useful when deciding whether or not to add new files to your music library
    Does not take any actions, just prints out the results
    Supports all configured audio formats via mutagen
    """
    es, index_name = estools.get_es()
    
    # Get supported extensions from settings
    supported_extensions_list = settings.get_setting("SUPPORTED_EXTENSIONS")
    supported_extensions = tuple(ext.lower() for ext in supported_extensions_list)

    files: List[str] = []
    for root, dirs, os_files in os.walk(dir_to_scan):
        for file in os_files:
            if file.lower().endswith(supported_extensions):
                files.append(os.path.join(root, file))

    files_to_import: List[Dict[str, Any]] = []

    for file in files:
        try:
            audiofile = MutagenFile(file)
            if not audiofile:
                print(f"Warning: Could not load {file}")
                continue
        except Exception as e:
            print(f"Error loading {file}: {e}")
            continue

        # Extract metadata using mutagen
        title = None
        artist = None
        album = None
        albumartist = None
        genre = None
        duration = 0
        bitrate = 0
        extension = os.path.splitext(file)[1].lower()

        if hasattr(audiofile, 'tags') and audiofile.tags:
            title = get_tag_value(audiofile, ['TIT2', 'TITLE', '\xa9nam'])
            artist = get_tag_value(audiofile, ['TPE1', 'ARTIST', '\xa9ART'])
            album = get_tag_value(audiofile, ['TALB', 'ALBUM', '\xa9alb'])
            albumartist = get_tag_value(audiofile, ['TPE2', 'ALBUMARTIST', 'aART'])
            genre = get_tag_value(audiofile, ['TCON', 'GENRE', '\xa9gen'])

        # Get audio info
        if hasattr(audiofile, 'info') and audiofile.info:
            duration = getattr(audiofile.info, 'length', 0)
            
            # Bitrate handling varies by format
            if hasattr(audiofile.info, 'bitrate'):
                bitrate = audiofile.info.bitrate
            elif hasattr(audiofile.info, 'bitrate_nominal'):
                bitrate = audiofile.info.bitrate_nominal

        if not title or not artist:
            print(f"Warning: Missing title or artist in {file}")
            continue

        size = os.path.getsize(file)
        if bitrate == 0 and duration > 0:
            bitrate = round((size * 8 / 1024) / duration, 0)

        query_body = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"artist": artist}},
                        {"match": {"title": title}},
                    ],
                    "should": [
                        {"range": {"bitrate": {"gte": 320}}},
                        {"range": {"samplerate": {"gte": 48000}}},
                    ],
                }
            },
            "size": 10000,
        }

        try:
            response = es.search(index=index_name, body=query_body)
            best_hit, best_hits, debug_info = estools.pick_best_hit(response)
            if best_hit is None:
                print(Fore.YELLOW + f"NEW: {file}")
                file_info = {
                    'path': file,
                    'title': title,
                    'artist': artist,
                    'album': album,
                    'albumartist': albumartist,
                    'genre': genre,
                    'bitrate': bitrate,
                    'extension': extension,
                    'old_bitrate': 0,
                    'reason': 'New'
                }
                files_to_import.append(file_info)
            else:
                old_bitrate = int(best_hit["hit"]["_source"].get("bitrate", 0))
                old_extension = best_hit["hit"]["_source"].get("extension", "").lower()
                
                # Use normalized bitrate comparison logic from estools
                new_normalized = estools.get_normalized_bitrate({'bitrate': bitrate, 'extension': extension})
                old_normalized = estools.get_normalized_bitrate({'bitrate': old_bitrate, 'extension': old_extension})
                
                # FLAC always wins
                is_new_flac = extension == '.flac'
                is_old_flac = old_extension == '.flac'
                
                should_replace = False
                reason = ""
                
                if is_new_flac and not is_old_flac:
                    should_replace = True
                    reason = "Format (FLAC vs non-FLAC)"
                elif not is_new_flac and is_old_flac:
                    should_replace = False
                elif is_new_flac and is_old_flac:
                    # Both FLAC, compare actual bitrates
                    if bitrate > old_bitrate:
                        should_replace = True
                        reason = "FLAC Bitrate"
                else:
                    # Both non-FLAC, compare normalized bitrates
                    if new_normalized > old_normalized:
                        should_replace = True
                        reason = "Normalized Bitrate"
                
                if should_replace:
                    print(Fore.RED + f"BETTER: {file}")
                    print(Fore.CYAN + f"  - Old: {old_extension} {old_bitrate}kbps (normalized: {old_normalized:.1f})")
                    print(Fore.CYAN + f"  - New: {extension} {bitrate}kbps (normalized: {new_normalized:.1f})")
                    print(Fore.YELLOW + f"  - Reason: {reason}")
                    
                    file_info = {
                        'path': file,
                        'title': title,
                        'artist': artist,
                        'album': album,
                        'albumartist': albumartist,
                        'genre': genre,
                        'bitrate': bitrate,
                        'extension': extension,
                        'old_bitrate': old_bitrate,
                        'old_extension': old_extension,
                        'reason': reason
                    }
                    files_to_import.append(file_info)
        except Exception as e:
            print(Fore.RED + f"An error occurred processing {file}: {e}")

    print(Fore.GREEN + "\nSummary:" + Style.RESET_ALL)
    for file_info in files_to_import:
        print(f"Artist:    {file_info['artist']}")
        print(f"Title:     {file_info['title']}")
        print(f"Album:     {file_info['album']}")
        print(f"Album A:   {file_info['albumartist']}")
        print(f"Genre:     {file_info['genre']}")
        print(f"Extension: {file_info['extension']}")
        print(f"Bitrate:   {file_info['bitrate']}")
        print(f"Old Bit:   {file_info['old_bitrate']}")
        if 'old_extension' in file_info:
            print(f"Old Ext:   {file_info['old_extension']}")
        print(f"Reason:    {file_info['reason']}")
        print(Fore.YELLOW + file_info['path'])
        print(Style.RESET_ALL)
