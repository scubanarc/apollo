# Apollo API v1

Base path: `/api/v1`. Responses are JSON. List endpoints accept integer `limit` (1–200, default 50) and `offset` (nonnegative, default 0), and return `items`, `total`, `limit`, and `offset`.

| Method | Endpoint | Result |
|---|---|---|
| GET | `/api/v1/` | API version, resources, operation names and fields |
| GET | `/api/v1/status` | Configuration/directory checks and worker availability; does not probe external services |
| POST | `/api/v1/restart` | Return `202`, then terminate Apollo so its service supervisor can restart it |
| GET | `/api/v1/playlists` | Source playlist names |
| GET | `/api/v1/playlists/<name>` | Source tracks; nested names supported |
| GET | `/api/v1/song?song=Artist%20-%20Title` | All matching indexed entries, selection reasons, current winner and rating summary |
| GET | `/api/v1/ratings` | Raw user ratings |
| GET | `/api/v1/votes` | Listening votes |
| GET | `/api/v1/skips` | Listening skips |
| GET | `/api/v1/jobs` | Job summaries, newest first |
| GET | `/api/v1/jobs/<id>` | Payload, status, result, error, timestamps and recent logs |
| POST | `/api/v1/jobs` | Validate and queue an operation |

## Authentication

Configure `APOLLO_API_TOKEN` in the server environment, or `API_TOKEN` in YAML. Send `Authorization: Bearer YOUR_TOKEN`. A configured token is required for all API routes. Token-authenticated mutations do not need CSRF tokens.

Without a configured token, local browser/session clients can read resources and send mutations with `X-CSRF-Token`, using the token from the page's `csrf-token` meta element and the same session cookie. CSRF validation remains enabled even when authentication is not configured.

`POST /api/v1/restart` is intentionally stricter: it requires a configured token and bearer authentication, even for a logged-in browser session. The response is sent before Apollo receives `SIGTERM`. The endpoint does not invoke systemd itself; use it only when Apollo runs under a supervisor configured to restart it. An active background job may be interrupted; inspect its status after Apollo returns before retrying it.

```http
POST /api/v1/restart
Authorization: Bearer YOUR_TOKEN
```

```json
{"status":"restarting"}
```

## Submit work

```http
POST /api/v1/jobs
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{"action":"playlist.preview","payload":{"type":"artist","input":"New Order","name":"night-drive"}}
```

A valid submission returns HTTP `202`, a `Location` header pointing to `/api/v1/jobs/<id>`, and:

```json
{
  "job": {
    "id": "generated-id",
    "action": "playlist.preview",
    "payload": {"type": "artist", "input": "New Order", "name": "night-drive"},
    "status": "queued",
    "created": "2026-09-10T12:00:00+00:00",
    "updated": "2026-09-10T12:00:00+00:00",
    "result": null,
    "error": null,
    "logs": []
  },
  "status_url": "/api/v1/jobs/generated-id",
  "worker_running": true
}
```

Poll `status_url`. States: `queued`, `running`, `succeeded`, `partial`, `failed`. `partial` means a completed scan/comparison/sync reported per-item failures. A failed operation may also have written some external data before failing; inspect its progress before submitting a new job. Results persist across server restarts. An offline worker leaves submissions queued.

## Operation payloads

Unknown fields and incorrect types are rejected. Boolean fields require JSON booleans. String fields, when supplied, must be nonempty and at most 10,000 characters, except raw playlist `content`, which may be empty and is limited by the 2 MiB request size. Omit optional strings instead of sending null or an empty string.

| Action | Payload |
|---|---|
| `playlist.preview` | Required `type` (`ai`, `artist`, `path`, `any`) and `input`; optional `name`. Returns `tracks`, `new_tracks`, `existing`, `name`; does not save. |
| `playlist.create` | Same fields, plus optional `dynamic` boolean. Appends new source tracks, or replaces and publishes the configured dynamic playlist. |
| `playlist.save` | Required `name` and `tracks` (1–10,000 `artist - title` strings, at most 2,000 characters each). Appends/de-duplicates these exact tracks. |
| `playlist.replace` | Required `name` and string `content`. Replaces an existing source `.txt` verbatim, including comments, whitespace, and duplicates. Empty content clears the file. Does not publish. |
| `playlist.stats` | Required `name` and string `content` (may be empty). Read-only analysis of this editor snapshot: song/artist counts, extra duplicate entries, comments, blank and invalid lines, top artists, library matches, formats, genre counts (unique songs and entries including repeats), genre-tag coverage, known duration with/without repeats, and shortest/longest songs. Library failures retain text statistics with a warning. Duration uses best-file matches before rating filters. Does not save or publish. |
| `playlist.publish` | Supply `name` or `all: true`, exclusively. Returns one result per playlist, including count, duration in seconds, output path and missing tracks. |
| `playlist.play` | Optional `name`; defaults to the configured dynamic playlist. Republishes that playlist, then sends its name to MQTT topic `nodered/playlist`. |
| `entry.play` | Required `entry_id`. Loads that exact Elasticsearch document and plays its file through MPD, with the same music-folder boundary validation. |
| `song.play` | Required `song` in `artist - title` form. Resolves Apollo's preferred indexed file, inserts its MPD-relative URI immediately after the current song, then skips forward to it. |
| `library.scan` | Optional `prune` boolean (default false). Returns total, updated, unchanged, deleted, errors, and whether pruning was skipped. |
| `library.compare` | Required `directory` on the server. Returns new/better file candidates, examined count, and metadata errors. |
| `ratings.calculate` | Empty payload calculates all songs. Optional `artist`, optional `title` with artist; or `artists: true` for all artist averages. `store: true` stores all-song calculations and cannot be combined with artist selection. |
| `ratings.sync` | Empty payload. Sends stored calculations to Navidrome; returns total/updated/unchanged/missing/failed counts and a failure-report path when relevant. |

Names are relative to the configured playlist root. Absolute paths, hidden components, traversal and escaping symlinks are rejected. `directory` is explicitly a server-side filesystem path for read-only comparison; this API is intended for the trusted library administrator.

To save a preview, submit `playlist.save` with the completed preview's `name` and `tracks`. Source mutations return `play_now_name`, and publication returns `play_now_names`; submit `playlist.play` with the selected name when the user accepts the offer. That operation publishes before requesting playback. This avoids regenerating an AI selection between review and save.

## Errors

```json
{"error":{"code":400,"message":"Choose a playlist type and provide input."}}
```

Validation/CSRF errors use `400`; missing authentication `401`; missing routes/jobs `404`; oversized bodies `413`; incorrect JSON media types `415`; unavailable read services or an unconfigured restart token use `503`. Runtime operation errors appear on the queued job rather than changing the original `202` response. The queue accepts up to 100 queued/running jobs at a time. Use `GET /api/v1/jobs/<id>` for detailed results; list endpoints omit large job payloads/results.
