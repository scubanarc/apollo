import eyed3
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

def scan_music_folder_into_es():
    """Scan MUSIC_FOLDER and upsert MP3 metadata into Elasticsearch."""
    input_directory = settings.get_setting("MUSIC_FOLDER")
    es_index = settings.get_setting("ES_INDEX")
    scanned_files = set()

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
        # us os.walk to get all the mp3 files
        for root, _, files in os.walk(input_directory):

            for file in files:
                if file.lower().endswith(".mp3"):
                    count += 1
                    mp3_file = os.path.join(root, file)
                    scanned_files.add(mp3_file)
                    print(f"\rProcessed {count} files...", end="", flush=True)

                    # Load the MP3 file
                    # tell eyeD3 to ignore unknown tags
                    eyed3.log.setLevel("ERROR")
                    
                    # before we load the file, let's get the file size and modification time
                    size = os.path.getsize(mp3_file)
                    modification_time = os.path.getmtime(mp3_file)

                    # now let's get the file from es, do not error if not found
                    doc = es.options(ignore_status=404).get(index=es_index, id=mp3_file)


                    # if the document exists, let's check the modification time
                    if doc["found"]:
                        if doc["_source"]["modification_time"] == modification_time:
                            #print(f"Skipping {mp3_file} - no changes")
                            continue
                    
                    print(Fore.RED + "New file: ", mp3_file)

                    # print("DEBUG - not skipping - test this upsert")
                    # quit()

                    audiofile = eyed3.load(mp3_file)

                    # Get the tags
                    if audiofile and audiofile.tag:
                        title = audiofile.tag.title
                        album = audiofile.tag.album
                        albumartist = audiofile.tag.album_artist
                        artist = audiofile.tag.artist
                        year = audiofile.tag.getBestDate()

                        if year:
                            year = year.year
                        else:
                            year = 0
                            
                        genre = audiofile.tag.genre
                        if genre:
                            genre = genre.name

                        # duration
                        duration = audiofile.info.time_secs

                        url = mp3_file
                        #bitrate_str = str(audiofile.info.bit_rate_str)
                        # bitrate returns a tuple, we only want the second element
                        bitrate = audiofile.info.bit_rate[1]
                        vbr = audiofile.info.bit_rate[0]

                        samplerate = str(audiofile.info.sample_freq)

                        if bitrate == 0:
                            # calc kbps from duration and filesize
                            bitrate = round((size * 8/ 1024) / duration, 0)

                        print(Fore.WHITE + f"  Title:        {title}")
                        print(f"  Album:        {album}")
                        print(f"  Album Artist: {albumartist}")
                        print(f"  Artist:       {artist}")
                        print(f"  Year:         {year}")
                        print(f"  Genre:        {genre}")
                        print(f"  URL:          {url}")
                        print(f"  Samplerate:   {samplerate}")
                        print(f"  Duration:     {duration}")
                        print(f"  Size:         {size}")
                        print(f"  Mod Time:     {modification_time}")
                        print(f"  VBR:          {vbr}")
                        print(Fore.YELLOW + f"  Bitrate:      {bitrate}")
                        print(Style.RESET_ALL)
                        
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
                                "size": size,
                                "modification_time": modification_time,
                                "vbr": vbr
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
        mp3_file = hit["_id"]
        if scanned_files and mp3_file not in scanned_files:
            print(Fore.RED + f"Missing {mp3_file}")
            es.delete(index=es_index, id=mp3_file)
            print(Fore.GREEN + f"Deleted {mp3_file}" + Style.RESET_ALL)
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
            
            if(song["artist"]):
                song["artist"] = song["artist"].replace("\"", "'")

            if(song["title"]):
                song["title"] = song["title"].replace("\"", "'")

            if(song["album"]):
                song["album"] = song["album"].replace("\"", "'")

            if(song["albumartist"]):
                song["albumartist"] = song["albumartist"].replace("\"", "'")

            if(song["genre"]):
                song["genre"] = song["genre"].replace("\"", "'")

            if(song["url"]):
                song["url"] = song["url"].replace("\"", "'")
                
            line = json.dumps(song) + "\n"
            line = remove_emojis(line)
            line = line.encode('utf-16','surrogatepass').decode('utf-16')
            line = line.encode('utf-8').decode('unicode-escape')
            line = unicodedata.normalize('NFKD', line).encode('ascii', 'ignore').decode('utf-8')

            # replace \x0 with a space
            line = line.replace("\x00", " ")

            # replace \ with escaped \
            line = line.replace("\\", "\\\\")

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