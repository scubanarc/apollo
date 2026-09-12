"""Contained paths and atomic UTF-8 writes."""

import os
import tempfile
from pathlib import Path


def contained(root: Path, relative: str) -> Path:
    if (
        not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or any(p in {".", ".."} or p.startswith(".") for p in Path(relative).parts)
    ):
        raise ValueError("Use a relative playlist name without hidden directories or '..'.")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Playlist path escapes its configured directory.")
    return result


def atomic_write(path: Path, content: str, mode: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
