import pytest

from apollo.jobs import run_worker
from apollo.web import create_app


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/playlists",
        "/library",
        "/ratings",
        "/activity",
        "/api",
        "/login",
        "/static/apollo.css",
        "/static/apollo.js",
        "/api/v1",
        "/api/v1/status",
        "/api/v1/playlists",
        "/api/v1/jobs",
    ],
)
def test_pages_start_without_integrations(client, path):
    result = client.get(path)
    assert result.status_code == 200, result.get_data(as_text=True)


def test_pages_include_dismissible_activity_viewer(client):
    page = client.get("/")
    assert b'<dialog id="activity-viewer"' in page.data
    assert b'id="activity-viewer-content"' in page.data
    assert b'aria-label="Close activity viewer"' in page.data
    script = client.get("/static/apollo.js").get_data(as_text=True)
    assert "event.preventDefault()" in script
    assert "submitter?.hasAttribute('formaction') ? submitter.formAction : form.action" in script
    assert "!/^\\/activity\\/[^/]+$/" in script
    assert "event.target === activityViewer" in script
    assert "activityViewer.addEventListener('close'" in script
    assert "'/actions/library.scan', '/actions/ratings.sync'" in script


def test_playlist_page_defaults_and_page_size(client, config):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    for number in range(105):
        (source / f"Playlist {number}.txt").write_text("A - B\n")

    page = client.get("/playlists")
    html = page.get_data(as_text=True)
    assert '<option value="any" selected>Metadata regex</option>' in html
    assert 'placeholder="Night drives' not in html
    assert 'placeholder="midnight-drive"' not in html
    assert html.count('class="mini-disc"') == 100
    assert "limit=100" in html and "offset=100" in html


def test_csrf_required(client):
    assert client.post("/actions/library.scan").status_code == 400
    assert (
        client.post("/api/v1/jobs", json={"action": "library.scan", "payload": {}}).status_code
        == 400
    )


def test_queue_and_complete_preview(client, csrf, app, service, config):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    cache = source / ".apollo/ai"
    cache.mkdir(parents=True)
    (cache / "es.jsonl").write_text('{"artist":"New Order","title":"Blue Monday"}\n')
    response = client.post(
        "/actions/playlist.preview",
        data={"csrf_token": csrf, "type": "any", "input": "New Order", "name": "night"},
    )
    assert response.status_code == 303
    store = app.extensions["apollo_jobs"]
    job = store.list()["items"][0]
    assert not (source / "night.txt").exists()
    run_worker(service, store, once=True)
    assert store.get(job["id"])["status"] == "succeeded"
    page = client.get(response.location)
    assert page.status_code == 200
    assert b"Blue Monday" in page.data and b"Save these tracks" in page.data
    assert b'action="/actions/song.play"' in page.data
    assert b'value="New Order - Blue Monday"' in page.data
    assert b">Play MPD</button>" in page.data
    save = client.post("/actions/playlist.save", data={"csrf_token": csrf, "preview_id": job["id"]})
    assert save.status_code == 303
    run_worker(service, store, once=True)
    assert "New Order - Blue Monday" in (source / "night.txt").read_text()
    assert client.get(save.location).status_code == 200
    assert client.get("/playlists/night").status_code == 200


def test_api_contract(client, csrf):
    response = client.post(
        "/api/v1/jobs",
        headers={"X-CSRF-Token": csrf},
        json={"action": "library.scan", "payload": {}},
    )
    assert response.status_code == 202
    assert response.json["job"]["status"] == "queued"
    assert response.headers["Location"] == response.json["status_url"]
    assert client.get(response.headers["Location"]).status_code == 200
    assert client.get("/api/v1/jobs/unknown").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"action": [], "payload": {}},
        {"action": "unknown", "payload": {}},
        {"action": "library.scan", "payload": {"prune": "false"}},
        {"action": "library.scan", "payload": {"command": "rm"}},
    ],
)
def test_invalid_payload(client, csrf, payload):
    response = client.post("/api/v1/jobs", headers={"X-CSRF-Token": csrf}, json=payload)
    assert response.status_code in {400, 415}
    assert "error" in response.json


def test_pagination(client):
    for query in ["limit=0", "limit=201", "offset=-1", "limit=no"]:
        assert client.get("/api/v1/jobs?" + query).status_code == 400
    assert client.get("/api/v1/jobs?limit=1&offset=0").json["limit"] == 1


def test_authentication(service):
    app = create_app(service, {"TESTING": True, "API_TOKEN": "secret-token"})
    client = app.test_client()
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/status").status_code == 401
    assert (
        client.get("/api/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    assert (
        client.post(
            "/api/v1/jobs",
            headers={"Authorization": "Bearer secret-token"},
            json={"action": "library.scan", "payload": {}},
        ).status_code
        == 202
    )
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    assert (
        client.post("/login", data={"token": "secret-token", "csrf_token": csrf}).status_code == 302
    )
    assert client.get("/").status_code == 200
    assert client.post("/actions/library.scan", data={"csrf_token": csrf}).status_code == 400


def test_restart_requires_bearer_token_and_runs_after_response(service):
    restarted = []
    app = create_app(
        service,
        {
            "TESTING": True,
            "API_TOKEN": "secret-token",
            "RESTART_CALLBACK": lambda: restarted.append(True),
        },
    )
    client = app.test_client()

    assert client.post("/api/v1/restart").status_code == 401
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["csrf"] = "csrf-token"
    assert client.post("/api/v1/restart", headers={"X-CSRF-Token": "csrf-token"}).status_code == 401
    response = client.post("/api/v1/restart", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 202
    assert response.json == {"status": "restarting"}
    assert restarted == []

    response.close()
    assert restarted == [True]


def test_restart_requires_configured_api_token(client):
    assert client.post("/api/v1/restart").status_code == 503


def test_escaped_playlist_names(client, config):
    (config.directory("PLAYLIST_SOURCE_FOLDER") / "<script>alert(1)<.txt").write_text(
        "Artist - Song\n"
    )
    text = client.get("/playlists").get_data(as_text=True)
    assert "&lt;script&gt;" in text
    assert "<script>alert(1)" not in text


def test_error_result_and_partial_pages(client, app):
    store = app.extensions["apollo_jobs"]
    for result in [
        {"stored": 5},
        {"items": [{"artist": "A", "songs": [{"title": "Song"}]}]},
        {"total": 3, "errors": [{"error": "broken"}]},
        {"tracks": ["A - B"], "new_tracks": ["A - B"], "existing": [], "name": "mix"},
    ]:
        job = store.submit(
            "playlist.preview" if "tracks" in result else "ratings.calculate",
            {"type": "any", "input": "x"} if "tracks" in result else {},
        )
        store.finish(job["id"], result=result)
        assert client.get("/activity/" + job["id"]).status_code == 200


def test_song_records_offer_playback(client, app):
    store = app.extensions["apollo_jobs"]
    job = store.submit("ratings.calculate", {})
    store.finish(job["id"], result={"items": [{"artist": "A", "title": "B", "rating": 5}]})
    page = client.get("/activity/" + job["id"])
    assert b'action="/actions/song.play"' in page.data
    assert b'value="A - B"' in page.data


def test_single_song_rating_result_offers_playback(client, app):
    store = app.extensions["apollo_jobs"]
    job = store.submit("ratings.calculate", {"artist": "A", "title": "B"})
    store.finish(job["id"], result={"artist": "A", "title": "B", "calculated_rating": 80})
    page = client.get("/activity/" + job["id"])
    assert b'action="/actions/song.play"' in page.data
    assert b'value="A - B"' in page.data


def test_raw_playlist_editor(client, csrf, config):
    from html import unescape

    source = config.directory("PLAYLIST_SOURCE_FOLDER") / "test.txt"
    content = (
        "\n# Keep this comment\n Z - Last  \n\nA - First\nZ - Last\n</textarea><script>x</script>\n"
    )
    content += "".join(f"Artist - Track {n}\n" for n in range(250))
    source.write_text(content)
    response = client.get("/playlists/test?limit=1&offset=100")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    textarea = html.split('wrap="off">', 1)[1].split("</textarea>", 1)[0]
    # HTML consumes the first newline immediately following a textarea start tag.
    assert unescape(textarea[1:]) == content
    assert "<script>x</script>" not in html
    assert source.read_text() == content
    edited = "\n# My comment\n Z - Last  \n\nA - First\nZ - Last\nfree text without a separator"
    response = client.post(
        "/playlists/test",
        data={
            "csrf_token": csrf,
            "content": edited.replace("\n", "\r\n"),
        },
    )
    assert response.status_code == 303
    assert source.read_bytes() == edited.encode()
    assert b"Playlist saved." in client.get(response.location).data
    assert not list(config.directory("PLAYLIST_PUBLISHED_FOLDER").iterdir())
    response = client.post("/playlists/test", data={"csrf_token": csrf, "content": ""})
    assert response.status_code == 303
    assert source.read_bytes() == b""


def test_playlist_list_and_editor_offer_playback(client, csrf, config):
    source = config.directory("PLAYLIST_SOURCE_FOLDER") / "1970.txt"
    source.write_text("A - B\n# comment\ninvalid\nC — D\nA - B\n")
    listing = client.get("/playlists")
    assert b"Play now" in listing.data
    assert b'value="1970"' in listing.data
    detail = client.get("/playlists/1970")
    assert b"Play now" in detail.data
    assert b'value="1970"' in detail.data
    assert b'id="playlist-view-toggle"' in detail.data
    assert b'aria-pressed="true">Editable Text</button>' in detail.data
    assert b'id="playlist-editor" hidden' in detail.data
    assert b'id="playlist-playable" aria-labelledby=' in detail.data
    assert detail.data.count(b'action="/actions/song.play"') == 3
    assert detail.data.count(b'value="A - B"') == 2
    assert b'value="C - D"' in detail.data
    response = client.post(
        "/playlists/1970",
        data={"csrf_token": csrf, "content": "C - D\n"},
    )
    assert response.status_code == 303
    page = client.get(response.location)
    assert b"Play now" in page.data


def test_created_and_published_job_results_offer_playback(client, app):
    store = app.extensions["apollo_jobs"]
    job = store.submit("playlist.save", {"name": "1970", "tracks": ["A - B"]})
    store.finish(job["id"], result={"name": "1970", "play_now_name": "1970"})
    page = client.get("/activity/" + job["id"])
    assert b"Play 1970 now" in page.data
    assert b"playlist.play" in page.data
    job = store.submit("playlist.publish", {"all": True})
    store.finish(job["id"], result={"items": [], "play_now_names": ["1970", "1980"]})
    page = client.get("/activity/" + job["id"])
    assert b"Play 1970 now" in page.data and b"Play 1980 now" in page.data


@pytest.mark.parametrize("content", ["# comment\r\nB - Song\r\n", "\n# comment\r\nB - Song\r"])
def test_raw_playlist_unchanged_save_preserves_bytes(client, csrf, config, content):
    path = config.directory("PLAYLIST_SOURCE_FOLDER") / "test.txt"
    path.write_bytes(content.encode())
    submitted = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    response = client.post("/playlists/test", data={"csrf_token": csrf, "content": submitted})
    assert response.status_code == 303
    assert path.read_bytes() == content.encode()


def test_raw_playlist_save_validation(client, csrf, config, tmp_path):
    source = config.directory("PLAYLIST_SOURCE_FOLDER")
    path = source / "test.txt"
    path.write_text("# keep me\n")
    assert client.post("/playlists/test", data={"content": "changed"}).status_code == 400
    assert client.post("/playlists/test", data={"csrf_token": csrf}).status_code == 400
    assert path.read_text() == "# keep me\n"
    assert (
        client.post(
            "/playlists/missing",
            data={
                "csrf_token": csrf,
                "content": "new",
            },
        ).status_code
        == 503
    )
    assert not (source / "missing.txt").exists()
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    (source / "linked.txt").symlink_to(outside)
    for name in ["../outside", "linked"]:
        assert (
            client.post(
                "/playlists/" + name,
                data={
                    "csrf_token": csrf,
                    "content": "changed",
                },
            ).status_code
            == 400
        )
    assert outside.read_text() == "untouched"
