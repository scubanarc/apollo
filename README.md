## The Apollo Concept

The core concept of Apollo is this:

> You should be able to maintain a list of your favorite **songs**, not **files**.

Apollo is a concept. It is a collection of scripts that implement a playlist creation system designed to streamline the process of  creating playlists from a collection of music files that you own.

> List of songs in -> Apollo -> M3U playlist out

Apollo creates M3U playlists from your existing music files based on either plain-text input files or AI. It does not download music, rename files, play music, or manage your library. Instead, it focuses on creating playlists based on your preferences.

> Apollo allows you to maintain lists of songs you like, and create M3U playlists on the fly based on those lists.

Apollo can co-exist with any existing system. It does not move or rename files. It simply creates playlists based on the files you already have. This means you can use Apollo alongside your existing music player or library management system without any conflicts.

> With Apollo, you can create dynamic playlist from any AI generated list of songs.

This creates a very spotify-like suggestion system, running on your own music files.

### What Apollo Does

Apollo was designed with music player daemons like `mpd` in mind, but it can be used with any music player that supports M3U playlists. 

When you manage a large collection of music files, it can be difficult to rename files without breaking playlists. Tools like Strawberry & MediaMonkey use a database to maintain a link between playlist entries and files. This makes renaming and moving files a fragile process, as playlists can break if files are renamed or moved.

With Apollo, you maintain lists of **songs** you like, as opposed to lists of **files** you like. Apollo uses Elasticsearch to match songs on the list to files in your collection, creating an M3U playlist from a list of songs. This allows you to rename and move music files without breaking playlists, as the playlists are based on song title rather than file paths.

In the Apollo system, all songs are referenced as "artist - title" pairs. Album is not considered, as Apollo is designed to work with individual songs rather than albums. This means that you can create playlists based on your favorite songs without worrying about the album they belong to.

A priority system is used to determine which file to use when multiple files match an artist-title pair. In the case of multiple files matching an artist-title pair, Apollo has a customizable preference system to determine which file to use. First, files of the highest bitrates are preferred. After that, filters can be applied to prioritize files based on filename patterns. For example, you can prioritize files with "live" or "accoustic" in the filename, or deprioritize files with "remix" or "cover" in the filename. This allows you to create playlists that reflect your preferences without worrying about file paths.

### Actions Apollo Makes Easy

The following actions are key concepts of Apollo.

#### Create a playist from a list of songs

Apollo will translate this:

```
Cake - The Distance
Spoon - The Underdog
Deadmau5 - Some Chords
```
into this:

```
#EXTM3U
/music/Cake/Fashion Nugget/CAKE - The Distance.mp3
/music/Spoon/Ga Ga Ga Ga Ga/Spoon - The Underdog.mp3
/music/deadmau5/4x4=12/deadmau5 - Some Chords.mp3
```
Notice how the playlist is based on the song title and artist, not the file path. This means you can rename or move files without breaking the playlist. Also note that case and small misspellings are handled by Elasticsearch, so you don't have to worry about getting the exact file path right.

This allows you to create playlists from a simple list of songs, which can be easily shared or used on different devices.

#### Create a playlist from a directory of music files

Let's say you copied a directory of music to your phone, and you fell in love with the playlist. You can use Apollo to create a playlist from that directory, which will be saved as an M3U file. This allows you to easily share the playlist with others or use it on different devices.

Simply run a directory list to a text file, clean it up a little, and then use Apollo to create the playlist. Here's a bash example:

```bash
find . -name '*.mp3' | sed 's-\.mp3--' | sed 's-^.*/--' > playlist.txt
```

#### Create a playlist from a spotify or youtube list

With libraries like `spotipy` or `youtube-dl`, you can create a list of songs from a Spotify or YouTube playlist. Apollo can then take that list and create an M3U playlist for you, allowing you to enjoy your favorite songs without worrying about file paths.

**Note:** Apollo does not download music from Spotify or YouTube. It only creates playlists based on the metadata of the songs in those playlists, based on files that you already have.

#### Create a playlist from an AI generated list

Apollo can also create playlists from AI-generated lists of songs. You can use an AI model to generate a list of songs based on your preferences, and Apollo will create an M3U playlist from that list.

Apollo includes scripts that can ask various AI models to generate a list of songs based on your preferences. This allows you to create playlists that reflect your musical taste without having to manually curate them.

AI useage is based on openrouter, so you can use any model that is available on openrouter. Some of the free models do a great job at this task. You can create playlists of songs by year, genre, mood, or any other criteria you can think of.

#### Maintain a list of songs you like, by both rating and skipping songs.

Apollo generates a dynamic rating for each song based on the following criteria:
- **Rating**: A rating from 1 to 5, where 1 is the lowest rating and 5 is the highest rating.
- **Good votes**: The number of times you have marked a song as "good"
- **Bad votes**: The number of times you have marked a song as "bad"
- **Skips**: The number of times you have skipped a song

There is a rating formula that is used to exclude files from created playlists. The default formula is:

```python
if not rating: rating = 2.5
rating = (rating * 20) + (good_votes * 5) - (bad_votes * 5) - (skips * 1)
```

This gives an unrated song a calculated rating of 50, a song with 5 good votes and no bad votes a rating of 75, and a song with 5 bad votes and no good votes a rating of 25. 

Skips are treated as negative votes in the rating formula, so a song that is skipped will have its rating decreased by 1 for each skip.

If a song's rating is below the configured threshold (default 45), it will be excluded from playlists.

All values are configurable in the `settings.yml` file, so you can adjust the rating formula to your liking.

## Installation

Currently, Apollo is just a collection of Python scripts. To use Apollo, you will need to have Python 3 installed on your system.

### Requirements

- Python 3.8 or higher
- pip (Python package installer)
- OpenRouter API key (for AI features)

You will need the following other services running:

- **Elasticsearch**: Apollo uses Elasticsearch to store and search for songs. You can run Elasticsearch locally, in a docker, or use a hosted service. Make sure to configure the connection settings in the `settings.yml` file.
- **MySQL**: Apollo uses MySQL to store ratings and skips. You will need to have a MySQL server running and create a database for Apollo. You can run MySQL locally or in a docker Make sure to configure the connection settings in the `settings.yml` file.

### Installation Steps

Clone the repository:

```bash
git clone https://github.com/scubanarc/apollo.git
cd apollo
```

Copy the `settings.yml` config file and `priority.yml` to `~/.config/apollo/` and customize them to your needs.

Install the required Python packages:

```bash
pip install -r requirements.txt
```

Create a virtual environment (optional but recommended):

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows use .venv\Scripts\activate
```

### Configuration

Apollo uses a YAML configuration file to store settings. You can find a sample settings.yml file in the repository. Copy it to `~/.config/apollo/settings.yml` and customize it to your needs. 

Copy `priority.yml` to `~/.config/apollo/priority.yml` and customize it to your needs. This file is used to determine the priority of files when multiple files match an artist-title pair. Positive values indicate higher priority, while negative values indicate lower priority. You can use this file to prioritize files based on filename patterns, such as preferring "live" or "acoustic" files over "remix" or "cover" files.

```PLAYLIST_SOURCE_FOLDER - Flat text files in 'artist - title' format
PLAYLIST_PUBLISHED_FOLDER - Where dynamically created M3U playlists are stored
DYNAMIC_PLAYLIST_FILE - The file where AI generated playlists are stored - considered temporary
```

### Indexing Your Music Files

After you have installed Apollo and configured the settings, you can start using it to create playlists. Apollo needs to know what files you have. It does this by indexing your music files into Elasticsearch. The first time you run this script, it will take some time to index your files. During this time, Apollo is testing each files bitrate, and storing the results in Elasticsearch.

Future updates are faster because Apollo only indexes new files or files that have changed since the last index. You can run the indexing script periodically to keep your music collection up to date.

Indexing is a bit like a sync, in that when new files are added, they are added to Elasticsearch, and when files are removed, they are removed from Elasticsearch. This means that you can add or remove files from your music collection without having to re-index everything.

To scan your music files and index them into Elasticsearch, run the following command:

```bash
apollo.py scan
```

You can rescan at any time.

## Usage

There are 2 main workflows for using Apollo: create playlists from lists of songs, or create playlists from AI.

### Create Playlists from Lists of Songs

You should maintain lists of your favorite songs in PLAYLIST_SOURCE_FOLDER. These files are simply 'artist - title' pairs, one per line. Apollo will read these files and create M3U playlists based on the songs in those files. You can create as many files as you like, and Apollo will create a playlist for each file.

Think of PLAYLIST_SOURCE_FOLDER as your master list of songs. You can create files like `favorites.txt`, `roadtrip.txt`, or `chill.txt`, and Apollo will create playlists based on those files.

### Create Playlists from AI

AI based playlists are created like this:

```bash
apollo.py create -t ai -i "classic rock songs from the 70s" -p "classic-rock-70s" -y
apollo.py create -t artist -i "spoon" -p "spoon-favorites" -y
apollo.py create -t any -i "happy" -p "happy-songs" -y
```

These commands will create "classic-rock-70s.txt", "spoon-favorites.txt", and "happy-songs.txt" in your PLAYLIST_SOURCE_FOLDER, and then create M3U playlists based on those files. The `-y` flag means "yes" to all prompts, so Apollo will automatically create the playlists without asking for confirmation.

**Note:** The .M3U playlists will be created in the PLAYLIST_PUBLISHED_FOLDER, which is configured in your `settings.yml` file, but not until you run the "publish" command.

AI playlists ask your AI agent to generate a list of songs that match your request. This is a way of replicating Spotify's "Discover" playlists. Simply ask the AI for 'classic rock songs from the 70s' or 'chill electronic music', and Apollo will create a playlist based on that request.

**Artist** playlists and **any** playlists do not use the AI, but instead use ElasticSearch to match your request. Artist matches only artist, while any matches any meta data associated with the song, such as title, album, or genre.

Finally, there is a `DYNAMIC_PLAYLIST_FILE` in your settings. This file is used to store AI generated playlists on the fly, so that mpd can pick them up right away.

## Typical Workflow

If you have not scanned your music files yet, run the following command:

```bash
./apollo.py scan
```

Then, create an AI playlist based on your music:

```bash
./apollo.py create -t ai -i "best of pop" -p pop -y
```

Finally, publish the playlists so that MPD can pick it up:

```bash
./apollo.py publish -p pop
```

There should now be a file called `pop.m3u` in your PLAYLIST_PUBLISHED_FOLDER, which you can use with your music player.

## Todo

This is very much a work in progress. It currently works for me, and I use it daily, but there are many features that could be added or improved. Here are some ideas for future development:

- Logging is a mess. There are print statements everywhere, and no real logging system. This needs to be cleaned up.


