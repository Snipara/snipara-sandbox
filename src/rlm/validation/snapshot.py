"""Bounded descriptor-relative source capture with normalized readable modes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
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
        ".docker",
        ".kube",
        ".gnupg",
        ".gcloud",
        ".npm",
        ".terraform",
        ".codex",
        ".claude",
        ".cache",
        "htmlcov",
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
        ".coverage",
        "coverage.xml",
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
        "secrets.yml",
        "credentials.yml",
        "credentials.yaml",
        "credentials.toml",
        "service-account.json",
    }
)
MAX_FILES = 20000
MAX_ENTRIES = 40000
MAX_PATH_BYTES = 1024 * 1024
MAX_BYTES = 64 * 1024 * 1024


class SnapshotCancelledError(RuntimeError):
    """The owner cancelled a source capture."""


def excluded(name: str) -> bool:
    lower = name.lower()
    return (
        lower in EXCLUDED_DIRS
        or lower in SECRET_NAMES
        or lower.startswith(".env")
        or lower.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def pin_directory(path: Path, *, root: Path | None = None) -> int:
    """Open every component with NOFOLLOW; callers own and must close the fd."""
    if root is None:
        absolute = path.absolute()
        anchor = Path(absolute.anchor)
        parts = absolute.relative_to(anchor).parts
    else:
        anchor = Path(root.anchor)
        parts = path.relative_to(anchor).parts
        path.relative_to(root)  # Refuse paths outside the already-authorized root.
    fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def capture(
    workspace: Path, destination: Path | None = None, *, workspace_fd: int | None = None
) -> dict[str, Any]:
    """Stream entries, preserve empty directories, and never follow source links."""
    if workspace_fd is None and workspace.is_symlink():
        raise ValueError("workspace must not be a symlink")
    source_fd = (
        os.dup(workspace_fd)
        if workspace_fd is not None
        else pin_directory(workspace.resolve(strict=True))
    )
    files: list[dict[str, Any]] = []
    directories: list[dict[str, Any]] = []
    omissions = []
    total = entries = path_bytes = 0

    def visit(fd: int, relative: Path, depth: int) -> None:
        nonlocal total, entries, path_bytes
        if depth > 64:
            raise ValueError("snapshot exceeds directory depth budget")
        directories.append({"path": relative.as_posix(), "mode": 0o755})
        if destination is not None:
            target = destination / relative
            target.mkdir(parents=True, exist_ok=True, mode=0o755)
            target.chmod(0o755)
        # scandir streams; inaccessible subtrees raise instead of disappearing.
        with os.scandir(fd) as iterator:
            for entry in iterator:
                item = (relative / entry.name).as_posix()
                entries += 1
                path_bytes += len(item.encode())
                if entries > MAX_ENTRIES or path_bytes > MAX_PATH_BYTES:
                    raise ValueError("snapshot exceeds entry/path budget")
                if excluded(entry.name):
                    omissions.append(item)
                    continue
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                    )
                    try:
                        visit(child, relative / entry.name, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if len(files) >= MAX_FILES or info.st_size + total > MAX_BYTES:
                        raise ValueError("snapshot exceeds file/byte budget")
                    with os.fdopen(
                        os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd),
                        "rb",
                    ) as source:
                        held = os.fstat(source.fileno())
                        if not stat.S_ISREG(held.st_mode) or held.st_nlink != 1:
                            raise ValueError("non-regular source refused")
                        data = source.read(MAX_BYTES - total + 1)
                    total += len(data)
                    if total > MAX_BYTES:
                        raise ValueError("snapshot exceeds byte budget")
                    mode = 0o755 if held.st_mode & 0o111 else 0o644
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
                        target.write_bytes(data)
                        target.chmod(mode)
                else:
                    raise ValueError("source link or special file refused: " + item)

    try:
        visit(source_fd, Path("."), 0)
    finally:
        os.close(source_fd)
    files.sort(key=lambda item: item["path"])
    directories.sort(key=lambda item: item["path"])
    identity = {
        "files": files,
        "directories": directories,
        "exclusion_policy": "secrets-and-generated-v2-normalized-modes",
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**identity, "excluded": sorted(omissions), "sha256": digest, "bytes": total}


def capture_bounded(
    workspace: Path,
    destination: Path | None = None,
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None = None,
    workspace_fd: int | None = None,
) -> dict[str, Any]:
    """A disposable helper makes blocked source I/O cancellable by its parent."""
    fd = (
        os.dup(workspace_fd)
        if workspace_fd is not None
        else pin_directory(workspace.resolve(strict=True))
    )
    script = "import runpy,json,sys; from pathlib import Path; m=runpy.run_path(sys.argv[1]); value=m['capture'](Path(sys.argv[2]),Path(sys.argv[4]) if sys.argv[4] else None,workspace_fd=int(sys.argv[3])); json.dump(value,sys.stdout,sort_keys=True)"
    env = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    }
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    str(Path(__file__).resolve()),
                    str(workspace),
                    str(fd),
                    str(destination) if destination is not None else "",
                ],
                pass_fds=(fd,),
                stdout=output,
                stderr=errors,
                env=env,
            )
            try:
                while process.poll() is None:
                    if cancelled is not None and cancelled():
                        raise SnapshotCancelledError("snapshot cancelled")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("snapshot deadline exceeded")
                    time.sleep(0.02)
                if process.returncode:
                    errors.seek(0)
                    raise ValueError(errors.read(4096).decode("utf-8", errors="replace"))
                if output.tell() > 8 * 1024 * 1024:
                    raise ValueError("snapshot manifest exceeds byte budget")
                output.seek(0)
                return json.load(output)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
    finally:
        os.close(fd)
