import os
import re
import json
from typing import List, Tuple, Optional
from colorama import Fore, Style
from apollo_lib import aitools
from apollo_lib import estools
from apollo_lib import settings

def get_tracks_by_type(ptype: str, input_str: str) -> List[str]:
    """Return track strings based on playlist type and input."""
    playlist_folder,apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()

    if ptype == "ai":
        result = aitools.get_playlist(input_str, 50)
        lines = result.split("\n")
        lines = list(set(lines))
        lines = [line for line in lines if not line.startswith('#')]
        lines.sort()
        es, index_name = estools.get_es()
        _urls, tracks, _duration, _missing = estools.get_playlist_from_lines(es, index_name, lines)
        return sorted(list(set(tracks)))

    if ptype == "artist":
        es, index_name = estools.get_es()
        tracks = estools.get_all_by_artist(es, index_name, input_str)
        return sorted(list(set(tracks)))

    if ptype == "any":
        pattern = re.compile(input_str, re.IGNORECASE)
        tracks: List[str] = []
        with open(os.path.join(ai_folder, "es.jsonl"), "r") as f:
            input_lines = f.readlines()
            for input_line in input_lines:
                try:
                    if pattern.search(input_line):
                        parsed = json.loads(input_line, strict=False)
                        artist = parsed["artist"]
                        title = parsed["title"]
                        tracks.append(f"{artist} - {title}")
                except Exception as e:
                    print(Fore.RED + "Caught error:", e)
                    print(Fore.YELLOW + "  ", input_line)
                    continue
        return sorted(list(set(tracks)))

    return []


def log_playlist_creation(input_str: str, tracks: str, date_time: str):
    """Write input, track list, and M3U for this run."""
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    with open(os.path.join(ai_folder, f"{date_time}.txt"), "w") as f:
        f.write(input_str + "\n\n")
        f.write("\n".join(tracks))
    with open(os.path.join(ai_folder, f"{date_time}.m3u"), "w") as f:
        playlist = "#EXTM3U\n" + "\n".join(tracks)
        f.write(playlist)
    return


def diff_against_default(tracks: List[str], default_playlist_file: str) -> Tuple[List[str], List[str]]:
    """Split tracks into existing vs new compared to default file."""
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    existing: List[str] = []
    source_path = os.path.join(playlist_folder, f"{default_playlist_file}.txt")
    if os.path.exists(source_path):
        with open(source_path, "r") as f:
            random_lines = f.readlines()
            random_lines = list(set(random_lines))
            random_lines.sort()
            for line in random_lines:
                line = line.strip()
                if line.startswith("#"):
                    continue
                if line in tracks:
                    tracks.remove(line)
                    existing.append(line)
    else:
        print(Fore.YELLOW + f"Note: default playlist file not found: {source_path}")
    return existing, tracks


def print_summary(ptype: str, input_str: str, existing: List[str], new_tracks: List[str]) -> None:
    """Print a concise summary of playlist results."""
    if ptype == "ai":
        print(Fore.YELLOW + "AI Playlist:")
    elif ptype == "artist":
        print(Fore.YELLOW + f"Artist: {input_str}")
    else:
        print(Fore.YELLOW + f"Search: {input_str}")
    for line in existing + new_tracks:
        pass
    print(Fore.CYAN + "\nExisting tracks:")
    for line in existing:
        print(f"{line}")
    print(Fore.GREEN + "\nNew tracks:")
    for line in new_tracks:
        print(f"{line}")
    print(Style.RESET_ALL)


def append_to_default_playlist(default_playlist_file: str, date_time: str, script_name: str, input_str: str, tracks: List[str]) -> None:
    """Append tracks to the configured default playlist file."""
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    print(f"Adding to {default_playlist_file} playlist")
    with open(os.path.join(playlist_folder, f"{default_playlist_file}.txt"), "a") as f:
        f.write("\n# Added at " + date_time)
        f.write(f"\n# apollo.py - \"{input_str}\"\n")
        for line in tracks:
            f.write(line + "\n")


def create_playlist(ptype: str, input_str: str, dynamic: bool, default_playlist_file: Optional[str], auto_yes: bool, date_time: Optional[str] = None) -> None:
    """End-to-end flow to build and optionally append a playlist."""
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    dt = date_time or os.popen("date +'%Y-%m-%d-%H-%M-%S'").read().strip()
    dpf = default_playlist_file or settings.get_setting('DEFAULT_PLAYLIST_FILE')
    tracks = get_tracks_by_type(ptype, input_str)
    log_playlist_creation(input_str, tracks, dt)

    # before pruning, write the dynamic playlist if requested
    if dynamic:
        dynamic_playlist_file = settings.get_setting('DYNAMIC_PLAYLIST_FILE')


        dynamic_path = os.path.join(playlist_folder, f"{dynamic_playlist_file}.txt")
        print(Fore.YELLOW + f"Writing dynamic playlist to {dynamic_path}")
        with open(dynamic_path, "w") as f:
            for line in tracks:
                f.write(line + "\n")
    
        write_m3u_files(single_file=dynamic_playlist_file + ".txt")
        return
    
    # normal playlist creation
    existing, tracks = diff_against_default(tracks, dpf)
    print_summary(ptype, input_str, existing, tracks)

    if len(tracks) == 0:
        print(Fore.RED + "No new tracks found.")
        print(Style.RESET_ALL)
        return
    
    write_to_default_playlist = auto_yes
    if not auto_yes:
        print(f"Do you want to add this to the '{dpf}' playlist? " + Fore.YELLOW + "(y/N): ")
        import getch as _getch
        key = _getch.getch()
        if key == 'y':
            write_to_default_playlist = True
    
    if write_to_default_playlist:
        script_name = os.path.basename(__file__)
        append_to_default_playlist(dpf, dt, script_name, input_str, tracks)
    else:
        print(f"Not adding to {dpf} playlist")
    

    print(Style.RESET_ALL)


def sort_source_playlists(playlist_folder, sorted_folder):
    """Normalize, de-duplicate, and sort source playlist .txt files."""
    print(Fore.YELLOW + "Sorting source playlists..." + Style.RESET_ALL)
    for root, dirs, filenames in os.walk(playlist_folder):
        sub = root.replace(playlist_folder, "").strip(os.sep)
        if(sub.startswith(".apollo")):
            continue
        for filename in filenames:
            file = os.path.join(root, filename)
            sorted_file = os.path.join(sorted_folder, filename)

            if file.lower().endswith(".txt"):
                with open(file, "r") as f:
                    lines = f.readlines()

                minimal_lines = []
                marker = set()
                for l in lines:
                    if l.startswith("#"):
                        continue
                    if l.startswith("\n"):
                        continue
                    ll = l.lower()
                    if ll not in marker:
                        marker.add(ll)
                        minimal_lines.append(l)
                    else:
                        t = l.strip()

                sorted_lines = sorted(minimal_lines, key=str.casefold)

                with open(sorted_file, "w") as f:
                    for line in sorted_lines:
                        f.write(line)

                new_size = len(sorted_lines)


def write_m3u_files(single_file: str | None = None):
    """Generate .m3u files (and missing lists) from sorted playlists."""
    playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder = settings.get_apollo_folders()
    es, index_name = estools.get_es()

    # Sort source playlists
    sort_source_playlists(playlist_folder, sorted_folder)

    if single_file:
        all_files = [single_file]
    else:
        all_files = os.listdir(sorted_folder)
        all_files.sort()

    for file in all_files:
        if file.endswith(".txt"):
            print(Fore.GREEN + f"Reading {file}")

            with open(os.path.join(sorted_folder, file), "r") as f:
                duration = 0
                lines = f.readlines()
                lines = [line for line in lines if not line.startswith("#")]
                lines = list(set(lines))

                urls, tracks, duration, missing = estools.get_playlist_from_lines(es, index_name, lines)

                urls.sort()
                line_count = len(urls)

                playlist = "#EXTM3U\n" + "\n".join(urls)

                duration = round(duration, 0)
                hours = round(duration // 3600, 0)
                minutes = round((duration % 3600) // 60, 0)
                duration = f"{hours} hours {minutes} minutes"

                print(Fore.YELLOW + f"  Duration: {duration}")
                print(Fore.YELLOW + f"  Count: {line_count}")
                print(Style.RESET_ALL)

                output_filename = file.replace(".txt", ".m3u")
                with open(os.path.join(m3u_folder, output_filename), "w") as f:
                    f.write(playlist)

                with open(os.path.join(missing_folder, file), "w") as f:
                    f.write("\n".join(missing))

                # copy the file to PLAYLIST_PUBLISHED_FOLDER
                published_folder = settings.get_setting("PLAYLIST_PUBLISHED_FOLDER")
                if published_folder:
                    published_file = os.path.join(published_folder, output_filename)
                    with open(published_file, "w") as f:
                        f.write(playlist)
                    print(Fore.YELLOW + f"  Published to {published_file}")
