"""Terminal interface; all music operations use ApolloService."""

import json
import logging
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from apollo.services import ApolloService, ServiceError
from apollo.settings import ConfigurationError, load_settings

app = typer.Typer(no_args_is_help=True, help="Playlists for your music library.")
rating_app = typer.Typer(no_args_is_help=True, help="Inspect, calculate and synchronize ratings.")
job_app = typer.Typer(no_args_is_help=True, help="Inspect background work.")
app.add_typer(rating_app, name="rating")
app.add_typer(job_app, name="jobs")
console = Console(stderr=True)


@app.callback()
def configure(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, help="Settings YAML path."),
    verbose: bool = False,
):
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")
    try:
        ctx.obj = ApolloService(load_settings(config))
    except ConfigurationError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(2) from exc


def output(result):
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def run(ctx, action, payload):
    try:
        result = ctx.obj.execute(action, payload)
        output(result)
        if isinstance(result, dict) and (result.get("failed") or result.get("errors")):
            raise typer.Exit(1)
        if isinstance(result, dict):
            names = result.get("play_now_names") or (
                [result["play_now_name"]] if result.get("play_now_name") else []
            )
            if len(names) == 1 and sys.stdin.isatty():
                if typer.confirm(f"Play {names[0]} now?", default=False):
                    output(ctx.obj.execute("playlist.play", {"name": names[0]}))
            elif names:
                commands = ", ".join(f"apollo play --name {name}" for name in names)
                console.print(f"Play now: {commands}", markup=False)
        return result
    except (ValueError, ServiceError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@app.command()
def doctor(ctx: typer.Context):
    """Check configuration, directories and read-only service connections."""
    result = ctx.obj.status(check_services=True)
    output(result)
    if not result["ok"]:
        raise typer.Exit(1)


@app.command()
def create(
    ctx: typer.Context,
    type: str = typer.Option(..., "--type", "-t"),
    input: str = typer.Option(..., "--input", "-i"),
    name: str | None = typer.Option(None, "--name", "-p"),
    dynamic: bool = False,
    preview: bool = False,
):
    """Create a source playlist, or preview without saving. Dynamic also publishes."""
    payload = {"type": type, "input": input}
    if name:
        payload["name"] = name
    if dynamic and preview:
        raise typer.BadParameter("Choose preview or dynamic creation.")
    if not preview:
        payload["dynamic"] = dynamic
    run(ctx, "playlist.preview" if preview else "playlist.create", payload)


@app.command()
def publish(
    ctx: typer.Context, name: str | None = typer.Option(None, "--name", "-p"), all: bool = False
):
    """Resolve source lists and publish M3U files."""
    run(ctx, "playlist.publish", {"name": name} if name else {"all": all})


@app.command()
def play(ctx: typer.Context, name: str | None = typer.Option(None, "--name", "-p")):
    """Publish and play a playlist via MQTT; defaults to dynamic."""
    run(ctx, "playlist.play", {"name": name} if name else {})


@app.command()
def scan(ctx: typer.Context, prune: bool = False):
    """Index audio metadata. --prune also removes missing files under MUSIC_FOLDER."""
    run(ctx, "library.scan", {"prune": prune})


@app.command()
def compare(ctx: typer.Context, directory: Path = typer.Option(..., "--directory", "-d")):
    """Find new songs or better file versions without changing music files."""
    run(ctx, "library.compare", {"directory": str(directory)})


@app.command("playlists")
def playlists(
    ctx: typer.Context,
    limit: int = typer.Option(50, min=1, max=200),
    offset: int = typer.Option(0, min=0),
):
    read(ctx, "playlists", limit, offset)


def read(ctx, resource, limit, offset):
    try:
        output(ctx.obj.read(resource, limit, offset))
    except (ValueError, ServiceError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@rating_app.command("list")
def rating_list(
    ctx: typer.Context,
    kind: str = "ratings",
    limit: int = typer.Option(50, min=1, max=200),
    offset: int = typer.Option(0, min=0),
):
    """List ratings, votes or skips."""
    if kind not in {"ratings", "votes", "skips"}:
        raise typer.BadParameter("kind must be ratings, votes or skips.")
    read(ctx, kind, limit, offset)


@rating_app.command("calculate")
def rating_calculate(
    ctx: typer.Context,
    artist: str | None = None,
    title: str | None = None,
    artists: bool = False,
    store: bool = False,
):
    """Calculate all songs, one artist/song, or all artist averages."""
    p = dict(artists=artists, store=store)
    if artist:
        p["artist"] = artist
    if title:
        p["title"] = title
    run(ctx, "ratings.calculate", p)


@rating_app.command("sync")
def rating_sync(ctx: typer.Context):
    """Push stored calculated ratings to Navidrome."""
    run(ctx, "ratings.sync", {})


@job_app.command("list")
def jobs_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, min=1, max=200),
    offset: int = typer.Option(0, min=0),
):
    from apollo.jobs import JobStore

    output(JobStore(ctx.obj.config.state_dir).list(limit, offset))


@job_app.command("show")
def jobs_show(ctx: typer.Context, identifier: str):
    from apollo.jobs import JobStore

    try:
        output(JobStore(ctx.obj.config.state_dir).get(identifier))
    except KeyError as exc:
        raise typer.BadParameter("Job not found.") from exc


@app.command()
def worker(ctx: typer.Context, once: bool = False):
    """Run the local queue worker; --once processes at most one queued job."""
    from apollo.jobs import JobStore, run_worker

    run_worker(ctx.obj, JobStore(ctx.obj.config.state_dir), once=once)


@app.command()
def serve(
    ctx: typer.Context,
    host: str = "0.0.0.0",
    port: int = typer.Option(5002, min=1, max=65535),
    worker: bool = True,
):
    """Serve HTML and API using Waitress; start a separate job worker by default."""
    from waitress import serve as serve_wsgi

    from apollo.web import create_app

    application = create_app(ctx.obj)
    child = None
    if worker:
        child = subprocess.Popen(
            [sys.executable, "-m", "apollo", "--config", str(ctx.obj.config.path), "worker"]
        )
    console.print(f"Apollo: http://{host}:{port}", markup=False)
    try:
        serve_wsgi(application, host=host, port=port, threads=4)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
