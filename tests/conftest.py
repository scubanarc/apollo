import pytest

from apollo.services import ApolloService
from apollo.settings import Settings
from apollo.web import create_app


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv("APOLLO_STATE_DIR", raising=False)
    monkeypatch.delenv("APOLLO_API_TOKEN", raising=False)
    for name in ("music", "source", "published"):
        (tmp_path / name).mkdir()
    return Settings(
        dict(
            MUSIC_FOLDER=str(tmp_path / "music"),
            PLAYLIST_SOURCE_FOLDER=str(tmp_path / "source"),
            PLAYLIST_PUBLISHED_FOLDER=str(tmp_path / "published"),
            STATE_DIR=str(tmp_path / "state"),
        ),
        tmp_path / "settings.yml",
    )


@pytest.fixture
def service(config):
    return ApolloService(config)


@pytest.fixture
def app(service):
    return create_app(service, {"TESTING": True, "SECRET_KEY": "tests"})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    client.get("/")
    with client.session_transaction() as session:
        return session["csrf"]
