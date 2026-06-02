## Project Overview

Apollo is a Python-based music playlist management system. It does not download or manage music files directly; instead, it creates playlists from song lists and maps those songs to files already in your library.

Core idea:
- input: a list of songs in `artist - title` form, or an AI-generated song list
- output: an `.m3u` playlist

Apollo is designed to work with existing music collections, Elasticsearch, MySQL, and optional Navidrome integration.

## Repository Layout

- `apollo.py` - main entry point
- `apollo_lib/cli.py` - CLI subcommands and argument parsing
- `apollo_lib/settings.py` - config loading from `~/.config/apollo/settings.yml`
- `apollo_lib/scanner.py` - scans music folders and indexes metadata into Elasticsearch
- `apollo_lib/playlist.py` - creates playlists, writes `.m3u`, handles source lists
- `apollo_lib/estools.py` - Elasticsearch lookup and file selection logic
- `apollo_lib/ratings.py` - song ratings, skips, votes, calculated ratings, MySQL storage
- `apollo_lib/navidrome.py` - Navidrome/Subsonic rating sync
- `apollo_lib/aitools.py` - OpenRouter/OpenAI-compatible AI playlist generation
- `apollo_lib/compare.py` - compares a directory against the index to find better versions
- `example/settings.yml` - sample configuration
- `README.md` - project overview and usage notes
- `CLAUDE.md`, `QWEN.md` - other agent instructions / repo notes

## Main Concepts

### Song Identity
Songs are referenced as `artist - title` pairs. Album is not the primary unit.

### Playlist Workflow
1. Scan music library into Elasticsearch
2. Create a source playlist from a text list or AI prompt
3. Resolve each song to the best matching file
4. Write `.m3u` playlist files
5. Optionally publish them to the configured output folder

### File Selection Rules
When multiple files match a song, Apollo chooses based on:
- FLAC preferred over non-FLAC
- higher bitrate preferred
- per-file priority patterns from `~/.config/apollo/priority.yml`
- normalized bitrate multipliers for non-FLAC formats

## Important External Dependencies

Apollo expects these services/configured systems:
- Elasticsearch for song indexing and search
- MySQL for rating/vote/skip storage
- OpenAI-compatible API via OpenRouter for AI playlists
- Navidrome for rating sync, if enabled
- mutagen for audio metadata parsing

## Configuration

Primary config file:
- `~/.config/apollo/settings.yml`

Important settings seen in the codebase:
- `MUSIC_FOLDER`
- `PLAYLIST_SOURCE_FOLDER`
- `PLAYLIST_PUBLISHED_FOLDER`
- `DYNAMIC_PLAYLIST_FILE`
- `DEFAULT_PLAYLIST_FILE`
- `ES_URL`
- `ES_INDEX`
- `DATABASE_HOST`
- `DATABASE_UN`
- `DATABASE_PWD`
- `DATABASE_NAME`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `NAVIDROME_URL`
- `NAVIDROME_UN`
- `NAVIDROME_PWD`
- `SUPPORTED_EXTENSIONS`
- `BITRATE_MULTIPLIERS`
- `RATING_THRESHOLD`
- `VOTE_STRENGTH`
- `SKIP_STRENGTH`

Apollo also expects:
- `~/.config/apollo/priority.yml` for filename priority patterns

## Common Commands

Run the CLI:
```bash
python apollo.py [command]
```

Or, if installed as a console script:
```bash
apollo [command]
```

Useful commands:
```bash
apollo scan
apollo create -t ai -i "classic rock songs from the 70s" -p "classic-rock-70s" -y
apollo create -t artist -i "Spoon" -p "spoon-favorites" -y
apollo create -t any -i "happy" -p "happy-songs" -y
apollo publish -p playlist-name
apollo publish -a
apollo compare -d /path/to/directory
apollo rating -ps
apollo rating -pv
apollo rating -pr
apollo rating -ca
apollo rating -c -a "Artist" -t "Title"
apollo rating -sn
```

## Development Workflow

- Use the existing `.venv` in `/src/apollo/.venv` when running Python locally in this repo.
- The codebase is a Python package installed in editable mode.
- Most behavior is controlled by config and external services rather than local state.
- There are several `__pycache__` and build artifacts in the repo; avoid editing generated files.

## Behavioral Notes

### Playlist creation
- `create` supports `ai`, `artist`, `path`, and `any` modes.
- AI mode asks an OpenAI-compatible API to generate songs in `artist - title` format.
- `artist` and `path` use Elasticsearch lookups.
- `any` scans `es.jsonl` with a regex match.

### Ratings
- Ratings are derived from a 1–5 user rating plus good votes, bad votes, and skips.
- Calculated ratings are filtered by `RATING_THRESHOLD` during playlist generation.
- Navidrome sync can push calculated ratings into the music server.

### Scanning
- Scanner uses mutagen for tags and audio info.
- It supports multiple audio formats from `SUPPORTED_EXTENSIONS`.
- Files are indexed into Elasticsearch by absolute file path.

### Publishing
- Source `.txt` playlists are normalized/sorted before generating `.m3u` output.
- Missing tracks are written to a companion missing-file list.
- Published output may be copied into `PLAYLIST_PUBLISHED_FOLDER`.

## Pitfalls / Gotchas

- Don’t assume Apollo manages files; it only creates playlists.
- Don’t edit generated artifacts like `__pycache__`, `build/`, or `.egg-info/`.
- `settings.py` exits if a required config key is missing.
- Elasticsearch and MySQL are required for most non-trivial workflows.
- AI playlists depend on an OpenAI-compatible API key and model setting.
- Navidrome sync expects a working Subsonic-compatible Navidrome server.

## Good Agent Behavior in This Repo

- Read the relevant module before making changes; logic is spread across small files.
- Prefer targeted edits over broad rewrites.
- Preserve the `artist - title` song format unless the user asks otherwise.
- When changing playlist matching or file selection, verify both `estools.py` and `playlist.py`.
- When changing rating behavior, check both `ratings.py` and `navidrome.py`.
- When changing config behavior, check `settings.py` and `example/settings.yml`.

## Quick Mental Model

Apollo is basically:
- index music metadata into Elasticsearch
- resolve song lists into best-file matches
- generate `.m3u` playlists
- optionally sync ratings to MySQL/Navidrome
- optionally use AI to create new song lists
