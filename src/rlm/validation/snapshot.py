"""Bounded, secret-filtered source capture; never execute in the source directory."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".snipara",
        ".ssh",
        ".aws",
        ".azure",
        ".config",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)
SECRET_NAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "id_rsa",
        "id_ed25519",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
    }
)
MAX_FILES = 20000
MAX_BYTES = 64 * 1024 * 1024


def excluded(name: str) -> bool:
    lower = name.lower()
    return (
        lower in EXCLUDED_DIRS
        or lower in SECRET_NAMES
        or lower.startswith(".env")
        or lower.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def capture(workspace: Path, destination: Path | None = None) -> dict[str, Any]:
    """Hash exact bytes with no-follow descriptor reads and optionally copy them."""
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("workspace must be an existing non-symlink directory")
    root = workspace.resolve(strict=True)
    files: list[dict[str, Any]] = []
    omissions: list[str] = []
    total = 0
    for folder, dirs, names, fd in os.fwalk(root, follow_symlinks=False):
        rel = Path(folder).relative_to(root)
        for name in sorted(dirs[:]):
            item = (rel / name).as_posix()
            if excluded(name):
                dirs.remove(name)
                omissions.append(item + "/")
            elif stat.S_ISLNK(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                raise ValueError(f"symlink directory refused: {item}")
        for name in sorted(names):
            item = (rel / name).as_posix()
            if excluded(name):
                omissions.append(item)
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"non-regular source refused: {item}")
            if len(files) >= MAX_FILES or info.st_size + total > MAX_BYTES:
                raise ValueError("snapshot exceeds file/byte budget")
            with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd), "rb") as source:
                data = source.read(MAX_BYTES - total + 1)
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError("snapshot exceeds byte budget")
            mode = 0o755 if info.st_mode & 0o111 else 0o644
            files.append(
                {
                    "path": item,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "mode": mode,
                }
            )
            if destination is not None:
                target = destination / item
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                target.write_bytes(data)
                target.chmod(mode)
    files.sort(key=lambda item: item["path"])
    manifest = {
        "files": files,
        "excluded": sorted(omissions),
        "exclusion_policy": "secrets-and-generated-v1",
    }
    return {
        **manifest,
        "sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "bytes": total,
    }
