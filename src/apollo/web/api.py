from flask import Blueprint, abort, current_app, jsonify, request, url_for

from apollo.services import OPERATIONS

api = Blueprint("api", __name__)


def service():
    return current_app.extensions["apollo_service"]


def jobs():
    return current_app.extensions["apollo_jobs"]


def pagination(default=50):
    try:
        limit = int(request.args.get("limit", default))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        abort(400, "limit and offset must be integers.")
    if not 1 <= limit <= 200 or offset < 0:
        abort(400, "limit must be 1–200; offset must be nonnegative.")
    return limit, offset


@api.get("")
@api.get("/")
def index():
    return jsonify(
        version=1,
        operations={key: sorted(value) for key, value in OPERATIONS.items()},
        endpoints=["status", "restart", "playlists", "song", "ratings", "votes", "skips", "jobs"],
        documentation="/api",
    )


@api.get("/status")
def status():
    return jsonify(**service().status(), worker_running=jobs().worker_alive())


@api.post("/restart")
def restart():
    response = jsonify(status="restarting")
    response.status_code = 202
    response.call_on_close(current_app.config["RESTART_CALLBACK"])
    return response


@api.get("/playlists")
def playlists():
    return jsonify(service().read("playlists", *pagination()))


@api.get("/playlists/<path:name>")
def playlist(name):
    return jsonify(service().read("playlist", *pagination(), name=name))


@api.get("/<kind>")
def records(kind):
    if kind not in {"ratings", "votes", "skips"}:
        abort(404)
    return jsonify(service().read(kind, *pagination()))


@api.get("/jobs")
def job_list():
    return jsonify(
        jobs().list(*pagination()),
    )


@api.get("/jobs/<identifier>")
def job_detail(identifier):
    try:
        return jsonify(jobs().get(identifier))
    except KeyError:
        abort(404, "Job not found.")


@api.post("/jobs")
def submit():
    body = request.get_json()
    if (
        not isinstance(body, dict)
        or set(body) != {"action", "payload"}
        or not isinstance(body.get("action"), str)
    ):
        abort(400, "Provide action and payload fields.")
    job = jobs().submit(body["action"], body["payload"])
    location = url_for("api.job_detail", identifier=job["id"])
    return (
        jsonify(job=job, status_url=location, worker_running=jobs().worker_alive()),
        202,
        {"Location": location},
    )


@api.get("/song")
def song_detail():
    return jsonify(service().read("song", name=request.args.get("song")))
