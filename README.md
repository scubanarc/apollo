# Apollo

Apollo builds playlists from music you already own. Search by artist, library path, metadata regex, or an AI prompt; resolve songs to the best files in your collection; and publish `.m3u` playlists. The same operations are available through a CLI, a Flask HTML interface, and a versioned JSON API.

Apollo indexes metadata in Elasticsearch and reads ratings, votes, and skips from MySQL. It can push calculated ratings to Navidrome. It does not download, move, or manage music files.

## Install and run

Python 3.12 or newer is required. From this repository:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/apollo doctor
.venv/bin/apollo serve
```

Open **http://localhost:5002/** locally, or `http://<server-ip>:5002/` from another device. Apollo listens on all interfaces (`0.0.0.0`) by default. `serve` uses Waitress and starts a separate local job worker. The server has no debugger or reloader enabled. The synthwave interface ships its CSS and JavaScript locally and requires no CDN.

`python -m apollo` and the installed `apollo` command invoke the same CLI. The old `apollo.py`, `apollo_lib`, and old rating flags have been removed.

## Configuration

Apollo reads `/mnt/fast/apollo/settings.yml`. Use `apollo --config /path/to/settings.yml …` or `APOLLO_CONFIG` to select another file. See [example/settings.yml](example/settings.yml) and [example/priority.yml](example/priority.yml).

Apollo also reads `APOLLO_API_TOKEN` from `apollo.env` beside the selected settings file. This overrides YAML `API_TOKEN`; the process environment takes precedence over both. The service uses `/mnt/fast/apollo/apollo.env`. Mount the shared directory at the same path on each host, and keep job state local to each instance.

Existing settings names are retained. Configuration is loaded explicitly; importing Apollo, opening help, or starting the dashboard does not connect to music services. Missing settings and inaccessible directories are reported when an operation needs them.

- `MUSIC_FOLDER`: an existing music directory, visible from the server host.
- `PLAYLIST_SOURCE_FOLDER`: existing directory containing `artist - title` source `.txt` files; the shared deployment uses `/mnt/fast/apollo/playlists`.
- `PLAYLIST_WORK_FOLDER`: optional existing directory for generated M3Us, sorted lists, missing-track reports, and the metadata cache. Defaults to `.apollo` beneath the source directory. The shared deployment uses `/mnt/fast/apollo/playlists/.apollo`; published M3Us remain at `/mnt/fast/playlists`.
- `PLAYLIST_PUBLISHED_FOLDER`: existing output directory, or an empty string to retain generated M3Us only in `.apollo/m3u`.
- `DEFAULT_PLAYLIST_FILE`, `DYNAMIC_PLAYLIST_FILE`: source playlist names without extensions.
- `ES_URL`, `ES_INDEX`: Elasticsearch 8 connection and index.
- `DATABASE_HOST`, `DATABASE_UN`, `DATABASE_PWD`, `DATABASE_NAME`: MySQL containing `apollo_rating`, `apollo_vote`, and `apollo_skip`. Apollo creates its derived `apollo_calculated_rating` table when storing calculations; it does not create or import listening-history tables.
- `AI_PROVIDER`: `openai` (OpenAI-compatible API) or `codex` (installed CLI). The former needs `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. The latter uses `CODEX_BIN` or `codex` on PATH, with optional `CODEX_HOME`, `CODEX_WORKDIR`, `CODEX_MODEL`, `CODEX_REASONING_EFFORT`, and `CODEX_TIMEOUT_SECONDS`. Authenticate the Codex CLI separately; Apollo does not copy credentials.
- `NAVIDROME_URL`, `NAVIDROME_UN`, `NAVIDROME_PWD`: optional rating-sync integration.
- `MPD_HOST`, `MPD_PORT`: direct MPD connection used to insert and play an individual song.
- `MQTT_SERVER`, `MQTT_PORT`, `MQTT_UN`, `MQTT_PWD`: broker connection used to play the dynamic playlist through Node-RED.
- `RATING_THRESHOLD`, `VOTE_STRENGTH`, `SKIP_STRENGTH`, `SUPPORTED_EXTENSIONS`, `BITRATE_MULTIPLIERS`: selection and calculation settings with defaults.
- `STATE_DIR`: defaults to the platform user state directory (`~/.local/state/apollo` on Linux). `APOLLO_STATE_DIR` overrides it. Holds the job queue, locks, failure reports, and persistent browser session secret. Use a local filesystem, with a separate directory per Apollo configuration.
- `API_TOKEN`: optional shared browser/API access token; `APOLLO_API_TOKEN` overrides it. With a token configured, browsers log in with it and API clients send `Authorization: Bearer …`. Set a token before exposing Apollo beyond localhost and use HTTPS through your reverse proxy. There are no separate user accounts.

The optional `priority.yml` is read beside the selected settings file; absent means no extra priority patterns. Only top Elasticsearch search-score matches compete: FLAC wins, then bitrate (normalized for lossy formats), then pattern priority. Song identity uses the explicit `artist - title` delimiter, preserving hyphens inside artist names.

`apollo doctor` checks directories, Elasticsearch, MySQL, Navidrome, and MPD using read-only requests. AI is checked for configuration/executable availability; doctor does not generate a playlist or incur API usage. `ffprobe` is provided by the OS `ffmpeg` package; if missing on Debian/Ubuntu, install it with `sudo apt install ffmpeg`.

## CLI workflows

```bash
apollo scan
apollo scan --prune
apollo create -t ai -i "synth-pop for a midnight drive" -p midnight --preview
apollo create -t artist -i "New Order" -p new-order
apollo create -t path -i "/music/Compilations" -p compilations
apollo create -t any -i "new wave|synth-pop" -p synth
apollo create -t ai -i "late night electronics" --dynamic
apollo playlists
apollo publish -p midnight
apollo publish --all
apollo play --name 1970
apollo compare -d /path/to/another/directory
apollo rating list --kind ratings
apollo rating list --kind votes --limit 100 --offset 0
apollo rating list --kind skips
apollo rating calculate
apollo rating calculate --artist "New Order"
apollo rating calculate --artist "New Order" --title "Blue Monday"
apollo rating calculate --artists
apollo rating calculate --store
apollo rating sync
apollo jobs list
apollo jobs show JOB_ID
```

CLI operations run synchronously and output JSON. Add the global `--verbose` option before the command for progress logging on stderr. Failed or partially failed operations exit nonzero. `create` saves immediately; `--preview` returns the selection without saving. The web preview saves the exact reviewed tracks, including AI responses, without regenerating them.

Creation appends new tracks to a source playlist. Publishing resolves source tracks to file paths and writes M3U and missing-track reports; it does not alter the source text. Dynamic creation replaces and immediately publishes the configured dynamic playlist. After creating, modifying, or publishing any playlist, Apollo offers to play it. The play action republishes its M3U, then sends the playlist name as the payload to MQTT topic `nodered/playlist` for Node-RED to load and start it. `apollo play` defaults to the configured dynamic playlist when `--name` is omitted. Nested source names are supported and preserved in output directories. Each file write is atomic; a multi-file publish or external sync is not an all-or-nothing transaction.

Scanning skips unchanged indexed files, updates new/changed metadata, and exports `.apollo/ai/es.jsonl` for regex searches. Index pruning is **opt-in** with `--prune`, limited to the configured root, and skipped after metadata failures. Empty/unavailable libraries or a changed traversal abort pruning. Music files are never removed.

## Web, API and workers

The web interface covers playlist creation/preview/publication, per-song playback, library scanning/comparison, rating inspection/calculation/storage, Navidrome sync, and job history. A song's Play link resolves Apollo's preferred file, inserts it immediately after MPD's current song, and skips forward to it. Long operations enter a persistent local SQLite queue and return immediately. Activity pages poll job progress and distinguish successful, partially successful, and failed results.

```bash
apollo serve --host 0.0.0.0 --port 5002
# Or run web and worker separately:
apollo serve --no-worker
apollo worker
# Process at most one queued job:
apollo worker --once
```

One worker per state directory processes jobs in order; duplicate workers exit. CLI and worker operations using the same state directory share an execution lock to prevent concurrent playlist writes. This lock does not coordinate different hosts; serialize edits to the same shared playlist across instances. Reads remain available while work runs. SQLite stores queued jobs and results; it does not replace MySQL for ratings. Interrupted running jobs become failed on worker restart, with a message to inspect partial changes before retrying. Queued jobs remain queued. Recent progress is bounded to 200 messages per job; job history/results persist until the state database is deliberately removed while Apollo is stopped.

For an external WSGI host, use `apollo.web:create_app()` and run `apollo worker` as a separate supervised process with the same configuration, state directory, user, and library mounts. `serve` is the convenient local launcher; stopping it gives its child worker ten seconds to finish before termination. A worker running separately finishes its current operation on SIGTERM. Do not place the SQLite queue on a network filesystem or share it across hosts.

API discovery: `GET /api/v1/`. Complete contracts and examples: [docs/api.md](docs/api.md). The in-app reference is at `/api`.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python -m build
```

Tests use temporary files and fake service clients; they do not write to your live Elasticsearch, MySQL, or Navidrome. See [docs/architecture.md](docs/architecture.md) for extension points and operational boundaries.
