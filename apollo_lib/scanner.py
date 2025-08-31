from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
import os
from colorama import Fore, Style
from elasticsearch import Elasticsearch, helpers
from elasticsearch.helpers import scan
import unicodedata
import re
import json
from apollo_lib import estools
from apollo_lib import settings

def remove_emojis(string):
    """Strip emoji characters from a string."""
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F" # emoticons
        u"\U0001F300-\U0001F5FF" # symbols & pictographs
        u"\U0001F680-\U0001F6FF" # transport & map symbols
        u"\U0001F1E0-\U0001F1FF" # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", 
        flags=re.UNICODE
    )
    
    return emoji_pattern.sub(r'', string)

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

def scan_music_folder_into_es():
    """Scan MUSIC_FOLDER and upsert audio file metadata into Elasticsearch."""
    input_directory = settings.get_setting("MUSIC_FOLDER")
    es_index = settings.get_setting("ES_INDEX")
    scanned_files = set()
    
    # Get supported audio file extensions from settings
    supported_extensions_list = settings.get_setting("SUPPORTED_EXTENSIONS")
    supported_extensions = tuple(ext.lower() for ext in supported_extensions_list)

    # turn off buffering
    os.environ['PYTHONUNBUFFERED'] = "1"

    # Connect to Elasticsearch
    es, index_name = estools.get_es()
    es_count = 0
    try:
        es_count = es.count(index=es_index, body={"query": {"match_all": {}}})["count"]
        print(Fore.CYAN + f"ElasticSearch currently has: {es_count} files" + Style.RESET_ALL)
    except Exception:
        print(Fore.CYAN + "ES count unavailable" + Style.RESET_ALL)
    
    count = 0
    new_songs = 0

    doc = None
    try:
        # us os.walk to get all the audio files
        for root, _, files in os.walk(input_directory):

            for file in files:
                if file.lower().endswith(supported_extensions):
                    count += 1
                    music_file = os.path.join(root, file)
                    scanned_files.add(music_file)
                    print(f"\rProcessed {count} files...", end="", flush=True)

                    # before we load the file, let's get the file size and modification time
                    file_size = os.path.getsize(music_file)
                    modification_time = os.path.getmtime(music_file)

                    # now let's get the file from es, do not error if not found
                    doc = es.options(ignore_status=404).get(index=es_index, id=music_file)


                    # if the document exists, let's check the modification time
                    if doc["found"]:
                        if doc["_source"]["modification_time"] == modification_time:
                            #print(f"Skipping {music_file} - no changes")
                            continue
                    
                    print(Fore.GREEN + "New file: ", music_file, Style.RESET_ALL)

                    # Load the audio file using mutagen
                    try:
                        audiofile = MutagenFile(music_file)
                        if not audiofile:
                            print(f"Warning: Could not load {music_file}")
                            continue
                    except Exception as e:
                        print(f"Error loading {music_file}: {e}")
                        continue

                    # Get the tags using mutagen's generic interface
                    title = None
                    album = None
                    albumartist = None
                    artist = None
                    year = 0
                    genre = None
                    duration = 0
                    bitrate = 0
                    samplerate = 0
                    vbr = False

                    # Extract metadata
                    if hasattr(audiofile, 'tags') and audiofile.tags:
                        # Common tag mappings for different formats
                        title = get_tag_value(audiofile, ['TIT2', 'TITLE', '\xa9nam'])
                        album = get_tag_value(audiofile, ['TALB', 'ALBUM', '\xa9alb'])
                        artist = get_tag_value(audiofile, ['TPE1', 'ARTIST', '\xa9ART'])
                        albumartist = get_tag_value(audiofile, ['TPE2', 'ALBUMARTIST', 'aART'])
                        
                        # Year/Date handling
                        year_str = get_tag_value(audiofile, ['TDRC', 'DATE', '\xa9day', 'YEAR'])
                        if year_str:
                            try:
                                # Extract year from various date formats
                                year_match = re.search(r'(\d{4})', str(year_str))
                                if year_match:
                                    year = int(year_match.group(1))
                            except (ValueError, AttributeError):
                                year = 0
                        
                        genre = get_tag_value(audiofile, ['TCON', 'GENRE', '\xa9gen'])

                    # Get audio info
                    if hasattr(audiofile, 'info') and audiofile.info:
                        duration = getattr(audiofile.info, 'length', 0)
                        
                        # Bitrate handling varies by format
                        if hasattr(audiofile.info, 'bitrate'):
                            bitrate = audiofile.info.bitrate
                        elif hasattr(audiofile.info, 'bitrate_nominal'):
                            bitrate = audiofile.info.bitrate_nominal
                        
                        # VBR detection
                        if hasattr(audiofile.info, 'bitrate_mode'):
                            vbr = audiofile.info.bitrate_mode != 0  # 0 is CBR
                        
                        # Sample rate
                        if hasattr(audiofile.info, 'sample_rate'):
                            samplerate = str(audiofile.info.sample_rate)
                        elif hasattr(audiofile.info, 'samplerate'):
                            samplerate = str(audiofile.info.samplerate)

                    url = music_file
                    # Extract file extension
                    extension = os.path.splitext(music_file)[1].lower()

                    if bitrate == 0 and duration > 0 or bitrate == 32000:
                        # make sure it's an MP3 file
                        if isinstance(audiofile, MP3):
                            # subtract ID3v2 tag size if present
                            try:
                                id3 = audiofile.tags
                                id3v2_size = id3.size  # bytes
                            except ID3NoHeaderError:
                                id3v2_size = 0

                            # subtract ID3v1 tag size if present (always 128 bytes at end)
                            id3v1_size = 128 if audiofile.tags and audiofile.tags.version == (1, 0) else 0

                            # calculate audio-only size
                            audio_size = file_size - id3v2_size - id3v1_size

                            # average bitrate in kbps
                            avg_bitrate = (audio_size * 8) / duration
                            bitrate = round(avg_bitrate / 1000) * 1000

                    print(Fore.WHITE + f"  Title:        {title}")
                    print(f"  Album:        {album}")
                    print(f"  Album Artist: {albumartist}")
                    print(f"  Artist:       {artist}")
                    print(f"  Year:         {year}")
                    print(f"  Genre:        {genre}")
                    print(f"  URL:          {url}")
                    print(f"  Samplerate:   {samplerate}")
                    print(f"  Duration:     {duration}")
                    print(f"  Size:         {file_size}")
                    print(f"  Mod Time:     {modification_time}")
                    print(f"  VBR:          {vbr}")
                    print(f"  Extension:    {extension}")
                    print(f"  Bitrate:      {bitrate}")
                    
                    # create json string to insert into Elasticsearch using upsert
                    update_body = {
                        "doc": {
                            "title": title,
                            "album": album,
                            "albumartist": albumartist,
                            "artist": artist,
                            "year": year,
                            "genre": genre,
                            "url": url,
                            "bitrate": bitrate,
                            "samplerate": samplerate,
                            "duration": duration,
                            "size": file_size,
                            "modification_time": modification_time,
                            "vbr": vbr,
                            "extension": extension
                        },
                        "doc_as_upsert": True
                    }

                    # insert the document into Elasticsearch using upsert and file path as the ID
                    es.update(index=es_index, id=url, body=update_body)
                    new_songs += 1
                    
    
        
        print(f"Total songs: {count}")
        print(f"New songs: {new_songs}")

        prune_missing_files_from_es(input_directory, scanned_files, es, es_index)

    except Exception as e:
        print(f"An error occurred: {e}")
        print(f"Document: {doc}")


def prune_missing_files_from_es(input_directory, scanned_files, es, es_index):
    """Delete ES docs for files no longer present on disk and write jsonl."""
    found = 0
    missing = 0
    output = ""
    count = 0
    es_count = 0

    try:
        es_count = es.count(index=es_index, body={"query": {"match_all": {}}})["count"]
        print(Fore.CYAN + f"ElasticSearch now has: {es_count} files" + Style.RESET_ALL)
    except Exception:
        print(Fore.CYAN + "ES count unavailable" + Style.RESET_ALL)

    for hit in scan(es, index=es_index, query={"query": {"match_all": {}}}):
        count += 1
        music_file = hit["_id"]
        if scanned_files and music_file not in scanned_files:
            print(Fore.RED + f"Missing {music_file}")
            es.delete(index=es_index, id=music_file)
            print(Fore.GREEN + f"Deleted {music_file}" + Style.RESET_ALL)
            missing += 1

        else:
            print(f"\rProcessed {count} files...", end="", flush=True)

            # create new object to hold a portion of the data
            song = {}
            song["artist"] = hit["_source"]["artist"]
            song["title"] = hit["_source"]["title"]
            song["album"] = hit["_source"]["album"]
            song["albumartist"] = hit["_source"]["albumartist"]
            song["year"] = hit["_source"]["year"]
            song["genre"] = hit["_source"]["genre"]
            song["url"] = hit["_source"]["url"]
            song["extension"] = hit["_source"].get("extension", "")
            song["bitrate"] = hit["_source"]["bitrate"]
            song["samplerate"] = hit["_source"]["samplerate"]
            song["duration"] = hit["_source"]["duration"]
            song["size"] = hit["_source"]["size"]
            song["vbr"] = hit["_source"]["vbr"]
            song["modification_time"] = hit["_source"]["modification_time"]
            song["id"] = hit["_id"]

            # json.dumps() already handles proper escaping, no manual escaping needed
                
            line = json.dumps(song, ensure_ascii=False) + "\n"

            output += line
            found += 1

    # write the output to a flat file
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    output_path = os.path.join(ai_folder, "es.jsonl")
    with open(output_path, "w") as f:
        f.write(output)
    
    print(Fore.YELLOW + f"\nFound: {found}")
    print(Fore.RED + f"Missing: {missing}")


    print(Fore.BLUE + f"Output written to: {output_path}" + Style.RESET_ALL)