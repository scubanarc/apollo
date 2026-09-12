# Architecture

```
src/apollo/
  __main__.py          python -m apollo entry point
  cli.py               Typer commands and JSON terminal presentation
  settings.py          explicit YAML configuration and defaults
  runtime.py           operation-scoped configuration, connections and caches
  services.py          shared use cases, input validation and execution lock
  playlist.py          preview, source saving and atomic publication
  playback.py          dynamic publication and MQTT playback request
  estools.py           Elasticsearch search and best-file selection
  scanner.py           metadata extraction, incremental index updates, pruning
  compare.py           read-only comparison using the same metadata reader
  ratings.py           MySQL reads, calculations and transactional storage
  navidrome.py         Subsonic lookup and rating synchronization
  aitools.py           OpenAI-compatible and Codex CLI providers
  files.py             contained paths and atomic UTF-8 writes
  jobs.py              persistent SQLite queue and single-process worker
  web/
    __init__.py        Flask factory, authentication, CSRF and errors
    pages.py           HTML routes and form adapters
    api.py             versioned JSON resources and job submission
    templates/         Jinja pages and shared components
    static/            local CSS and JavaScript
```

Both frontends use `ApolloService`; neither shells out to the other. Services return JSON-compatible data and raise explicit errors. Logging is separate from results. A context scoped to each service operation supplies the existing domain/integration modules with that operation's settings, connection cleanup, and caches. Context variables isolate concurrent reads and multiple app instances; caches never survive an operation. Flask services and job stores are injected through `app.extensions`, so tests can create independent apps.

Add a capability by implementing its domain behavior, declaring its accepted fields and validation in `services.py`, and adding a service dispatch branch. Add CLI arguments and an HTML form as adapters. Long operations use the existing job submission route; new read resources get their own API route. Keep validation in the application layer, with presentation-specific parsing at the boundary. Do not add Flask or terminal dependencies to domain logic.

The local queue deliberately has a single execution worker. An advisory lock serializes CLI and queued operations that can modify derived files or external state. The worker lock prevents duplicate local workers; recovery marks interrupted work failed instead of automatically repeating potentially partial external writes. No job retries are automatic. A separate queue implementation can replace `JobStore` and `run_worker` if distributed execution becomes necessary.

Configuration is loaded once per CLI/server/worker instance; restart to apply changes. Missing settings are validated when required. The dashboard reports availability without connecting to external systems; doctor explicitly checks connections. A malformed YAML file still needs correction before startup. Neither routine page loads nor tests scan or change a music library.

Source and output paths are contained in configured directories, including symlink resolution, and writes replace individual files atomically. This prevents partial individual files; multiple output files and external services cannot share one transaction. Playlist creation, publication, indexing and synchronization can therefore report partial progress on failure. Never advertise them as transactionally reversible.

Authentication is optional for local use and uses one configured shared access token. When configured it gates both browser and API requests. Browser mutations always require a session CSRF token, including login. Bearer-authenticated requests do not require browser CSRF tokens. CORS is not enabled, secrets are excluded from status, and remote HTTP errors are sanitized before entering job results. The persistent session secret lives in private local state.
