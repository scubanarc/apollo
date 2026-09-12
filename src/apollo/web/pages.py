from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from apollo.services import OPERATIONS, ServiceError
from apollo.web.api import jobs, pagination, service

pages = Blueprint("pages", __name__)


@pages.get("/")
def dashboard():
    return render_template(
        "dashboard.html",
        status=service().status(),
        worker=jobs().worker_alive(),
        recent=jobs().list(5)["items"],
    )


@pages.get("/playlists")
def playlists():
    error, result = None, dict(items=[], total=0, limit=100, offset=0)
    try:
        result = service().read("playlists", *pagination(100))
    except (ValueError, ServiceError) as exc:
        error = str(exc)
    return render_template("playlists.html", result=result, error=error)


@pages.get("/playlists/<path:name>")
def playlist_detail(name):
    result = service().read("playlist.source", name=name)
    return render_template("playlist.html", result=result)


@pages.post("/playlists/<path:name>")
def playlist_edit(name):
    if "content" not in request.form:
        abort(400, "Playlist content is required.")
    original = service().read("playlist.source", name=name)["content"]
    # Browsers normalize textarea line endings; retain the source convention.
    content = request.form["content"].replace("\r\n", "\n").replace("\r", "\n")
    normalized = original.replace("\r\n", "\n").replace("\r", "\n")
    if content == normalized:
        content = original
    elif "\r\n" in original and "\n" not in original.replace("\r\n", ""):
        content = content.replace("\n", "\r\n")
    elif "\r" in original and "\n" not in original:
        content = content.replace("\n", "\r")
    service().execute("playlist.replace", {"name": name, "content": content})
    flash("Playlist saved.")
    return redirect(url_for("pages.playlist_detail", name=name), code=303)


@pages.get("/library")
def library():
    return render_template("library.html")


@pages.get("/ratings")
def ratings():
    kind = request.args.get("kind", "ratings")
    if kind not in {"ratings", "votes", "skips"}:
        abort(400, "Unknown rating view.")
    result, error = None, None
    if request.args.get("load"):
        try:
            result = service().read(kind, *pagination())
        except (ValueError, ServiceError) as exc:
            error = str(exc)
    return render_template("ratings.html", result=result, error=error, kind=kind)


@pages.get("/activity")
def activity():
    return render_template(
        "activity.html", result=jobs().list(*pagination()), worker=jobs().worker_alive()
    )


@pages.get("/activity/<identifier>")
def job_detail(identifier):
    try:
        job = jobs().get(identifier)
    except KeyError:
        abort(404, "Job not found.")
    limit, offset = pagination()
    result = job.get("result")
    page = None
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        page = dict(
            items=result["items"][offset : offset + limit],
            total=len(result["items"]),
            limit=limit,
            offset=offset,
        )
    return render_template("job.html", job=job, result_page=page)


@pages.post("/actions/<action>")
def submit(action):
    if action not in OPERATIONS:
        abort(404)
    payload = {}
    for key in OPERATIONS[action]:
        value = request.form.get(key)
        if value or (key == "content" and value is not None):
            payload[key] = (
                value == "on" if key in {"dynamic", "all", "prune", "artists", "store"} else value
            )
    if action == "playlist.save":
        # Save the exact preview, never regenerate an AI response on confirmation.
        try:
            preview = jobs().get(request.form.get("preview_id", ""))
        except KeyError:
            abort(400, "Preview not found.")
        if preview["action"] != "playlist.preview" or preview["status"] != "succeeded":
            abort(400, "Wait for the preview to finish.")
        payload = {"name": preview["result"]["name"], "tracks": preview["result"]["tracks"]}
    job = jobs().submit(action, payload)
    return redirect(url_for("pages.job_detail", identifier=job["id"]), code=303)


@pages.get("/api")
def api_docs():
    return render_template("api.html", operations=OPERATIONS)


@pages.get("/song")
def song_detail():
    return render_template("song.html", result=service().read("song", name=request.args.get("song")))


@pages.get("/stream")
def stream():
    try:
        path = service().stream_path(
            song=request.args.get("song"), entry_id=request.args.get("entry_id")
        )
        return send_file(path, conditional=True, as_attachment=False)
    except FileNotFoundError:
        abort(404, "Audio file not found.")
