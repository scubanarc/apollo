import yaml
from typer.testing import CliRunner

from apollo.cli import app
from apollo.jobs import JobStore, run_worker


def test_queue_survives_restart_and_recovers(config, service):
    store = JobStore(config.state_dir)
    queued = store.submit("library.scan", {})
    reopened = JobStore(config.state_dir)
    assert reopened.get(queued["id"])["status"] == "queued"
    reopened.claim()
    reopened.recover()
    assert reopened.get(queued["id"])["status"] == "failed"
    assert "partial changes" in reopened.get(queued["id"])["error"]


def test_worker_failure_is_visible(config, service):
    store = JobStore(config.state_dir)
    job = store.submit("library.scan", {})
    run_worker(service, store, once=True)
    assert store.get(job["id"])["status"] == "failed"
    assert "No supported music" in store.get(job["id"])["error"]


def test_cli_uses_service_and_error_exit(config):
    config.path.write_text(yaml.safe_dump(config.values))
    runner = CliRunner()
    result = runner.invoke(app, ["--config", str(config.path), "scan"])
    assert result.exit_code == 1
    assert "No supported music" in result.output
    result = runner.invoke(
        app, ["--config", str(config.path), "create", "--type", "bad", "--input", "x"]
    )
    assert result.exit_code == 1
    assert "playlist type" in result.output
    result = runner.invoke(app, ["--config", str(config.path), "playlists"])
    assert result.exit_code == 0 and '"total": 0' in result.output


def test_cli_help_without_config(tmp_path):
    result = CliRunner().invoke(app, ["--config", str(tmp_path / "none"), "--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
