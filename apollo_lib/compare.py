import os
from typing import List
import eyed3
from colorama import Fore, Style
from apollo_lib import estools


def compare_directory(dir_to_scan: str) -> None:
    """
    Compare the contents of a directory against the Elasticsearch index
    Useful when deciding whether or not to add new files to your music library
    Does not take any actions, just prints out the results
    This is incomplete code, but it does work
    """
    es, index_name = estools.get_es()

    files: List[str] = []
    for root, dirs, os_files in os.walk(dir_to_scan):
        for file in os_files:
            if file.lower().endswith(".mp3"):
                files.append(os.path.join(root, file))

    eyed3.log.setLevel("ERROR")

    files_to_import: List[eyed3.core.AudioFile] = []

    for file in files:
        audiofile = eyed3.load(file)
        if not (audiofile and audiofile.tag and audiofile.info):
            continue

        title = audiofile.tag.title
        artist = audiofile.tag.artist

        size = os.path.getsize(file)
        duration = audiofile.info.time_secs
        bitrate = audiofile.info.bit_rate[1]
        if bitrate == 0 and duration:
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
                audiofile.old_bitrate = 0
                audiofile.reason = "New"
                files_to_import.append(audiofile)
            else:
                old_bitrate = int(best_hit["hit"]["_source"].get("bitrate", 0))
                if old_bitrate >= int(bitrate):
                    continue
                else:
                    print(Fore.RED + f"BETTER: {file}")
                    print(Fore.CYAN + f"  - Old bitrate: {old_bitrate}")
                    print(Fore.CYAN + f"  - New bitrate: {bitrate}")
                    print(Fore.YELLOW + "  - Existing bitrate is lower than new bitrate, update this file")
                    audiofile.old_bitrate = old_bitrate
                    audiofile.reason = "Bitrate"
                    files_to_import.append(audiofile)
        except Exception as e:
            print(Fore.RED + f"An error occurred: {e}")

    print(Fore.GREEN + "\nSummary:" + Style.RESET_ALL)
    for file in files_to_import:
        print(f"Artist:  {file.tag.artist}")
        print(f"Title:   {file.tag.title}")
        print(f"Album:   {file.tag.album}")
        print(f"Album A: {file.tag.album_artist}")
        print(f"Genre:   {file.tag.genre}")
        print(f"Bitrate: {file.info.bit_rate[1]}")
        print(f"Old Bit: {file.old_bitrate}")
        print(f"Reason:  {file.reason}")
        print(Fore.YELLOW + str(file))
        print(Style.RESET_ALL)
