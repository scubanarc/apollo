"""Validated configuration, loaded explicitly at the application boundary."""

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from platformdirs import user_state_dir

DEFAULT_CONFIG = Path("/mnt/fast/apollo/settings.yml")


class ConfigurationError(ValueError):
    pass


DEFAULTS = {
    "DEFAULT_PLAYLIST_FILE": "random",
    "DYNAMIC_PLAYLIST_FILE": "dynamic",
    "ES_INDEX": "apollo",
    "AI_PROVIDER": "openai",
    "VOTE_STRENGTH": 5,
    "SKIP_STRENGTH": 1,
    "RATING_THRESHOLD": 45,
    "SUPPORTED_EXTENSIONS": [".mp3", ".flac", ".ogg", ".m4a", ".mp4"],
    "BITRATE_MULTIPLIERS": {
        ".mp3": 1.0,
        ".ogg": 1.3,
        ".m4a": 1.2,
        ".aac": 1.2,
        ".mp4": 1.2,
        ".flac": 1.0,
    },
    "CODEX_TIMEOUT_SECONDS": 120,
}


@dataclass(frozen=True)
class Settings:
    values: dict[str, Any] = field(repr=False)
    path: Path = field(default_factory=lambda: DEFAULT_CONFIG)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, DEFAULTS.get(key, default))

    def require(self, key: str) -> Any:
        value = self.get(key)
        if value is None or value == "":
            raise ConfigurationError(f"Configure {key} in {self.path}.")
        return value

    def directory(self, key: str) -> Path:
        path = Path(self.require(key)).expanduser().absolute()
        if not path.is_dir():
            raise ConfigurationError(f"{key} is unavailable: {path}. Check the directory or mount.")
        return path

    @property
    def state_dir(self) -> Path:
        return (
            Path(
                os.environ.get("APOLLO_STATE_DIR")
                or self.get("STATE_DIR", user_state_dir("apollo"))
            )
            .expanduser()
            .absolute()
        )


def load_settings(path: Path | None = None) -> Settings:
    path = (path or Path(os.environ.get("APOLLO_CONFIG", DEFAULT_CONFIG))).expanduser().absolute()
    try:
        values = yaml.safe_load(path.read_text()) if path.exists() else {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            f"Cannot read configuration at {path} ({type(exc).__name__})."
        ) from exc
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ConfigurationError("Settings must be a YAML mapping.")
    # Read the token beside this configuration without mutating process-wide
    # environment or leaking credentials between independently loaded configs.
    env_path = path.with_name("apollo.env")
    try:
        if env_path.exists():
            token = dotenv_values(env_path, interpolate=False).get("APOLLO_API_TOKEN")
            if token:
                values["API_TOKEN"] = token
    except OSError as exc:
        raise ConfigurationError(f"Cannot read credentials at {env_path}.") from exc
    string_keys = (
        "MUSIC_FOLDER",
        "PLAYLIST_SOURCE_FOLDER",
        "PLAYLIST_WORK_FOLDER",
        "PLAYLIST_PUBLISHED_FOLDER",
        "STATE_DIR",
        "DEFAULT_PLAYLIST_FILE",
        "DYNAMIC_PLAYLIST_FILE",
        "ES_URL",
        "ES_INDEX",
        "DATABASE_HOST",
        "DATABASE_UN",
        "DATABASE_PWD",
        "DATABASE_NAME",
        "NAVIDROME_URL",
        "NAVIDROME_UN",
        "NAVIDROME_PWD",
        "MPD_HOST",
        "MQTT_SERVER",
        "MQTT_UN",
        "MQTT_PWD",
        "AI_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "API_TOKEN",
        "CODEX_BIN",
        "CODEX_HOME",
        "CODEX_WORKDIR",
        "CODEX_MODEL",
        "CODEX_REASONING_EFFORT",
    )
    for key in string_keys:
        if key in values and not isinstance(values[key], str):
            raise ConfigurationError(f"{key} must be a string.")
    if values.get("AI_PROVIDER", "openai") not in {"openai", "codex"}:
        raise ConfigurationError("AI_PROVIDER must be openai or codex.")
    for key in ("VOTE_STRENGTH", "SKIP_STRENGTH", "RATING_THRESHOLD", "CODEX_TIMEOUT_SECONDS"):
        if key in values and (
            isinstance(values[key], bool) or not isinstance(values[key], (int, float))
        ):
            raise ConfigurationError(f"{key} must be a number.")
    for key in ("MPD_PORT", "MQTT_PORT"):
        if key in values and (
            isinstance(values[key], bool)
            or not isinstance(values[key], int)
            or not 1 <= values[key] <= 65535
        ):
            raise ConfigurationError(f"{key} must be an integer from 1 to 65535.")
    extensions = values.get("SUPPORTED_EXTENSIONS", DEFAULTS["SUPPORTED_EXTENSIONS"])
    if (
        not isinstance(extensions, list)
        or not extensions
        or not all(isinstance(x, str) and x.startswith(".") for x in extensions)
    ):
        raise ConfigurationError("SUPPORTED_EXTENSIONS must be a nonempty list of extensions.")
    # A blank YAML setting means no custom multipliers, just like an omitted key.
    # Normalize the stored value too so matching receives a mapping, never None.
    multipliers = values.get("BITRATE_MULTIPLIERS")
    if multipliers is None:
        multipliers = DEFAULTS["BITRATE_MULTIPLIERS"].copy()
        values["BITRATE_MULTIPLIERS"] = multipliers
    if not isinstance(multipliers, dict) or not all(
        isinstance(v, (int, float)) and v > 0 for v in multipliers.values()
    ):
        raise ConfigurationError("BITRATE_MULTIPLIERS must map extensions to positive numbers.")
    return Settings(values, path)


# Each service operation gets its own context, including independently injected settings.
_current: ContextVar[Settings] = ContextVar("apollo_settings")


def current() -> Settings:
    return _current.get()


def get_setting(key: str, default: Any = None) -> Any:
    return current().get(key, default) if default is not None else current().require(key)


def get_apollo_folders():
    source = current().directory("PLAYLIST_SOURCE_FOLDER")
    configured_work = current().get("PLAYLIST_WORK_FOLDER")
    work = current().directory("PLAYLIST_WORK_FOLDER") if configured_work else source / ".apollo"
    if not configured_work and not work.resolve().is_relative_to(source.resolve()):
        raise ConfigurationError(
            "The .apollo working directory must stay inside PLAYLIST_SOURCE_FOLDER."
        )
    folders = [work, work / "ai", work / "m3u", work / "missing", work / "sorted"]
    for folder in folders:
        if not folder.resolve().is_relative_to(work.resolve()):
            raise ConfigurationError("Working directories must stay inside the working folder.")
        folder.mkdir(parents=True, exist_ok=True)
    return tuple(str(p) for p in [source, *folders])
