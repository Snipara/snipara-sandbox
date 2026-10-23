"""Descriptor-owned state directories: paths are never reopened after a check."""

from __future__ import annotations

import fcntl
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rlm.validation.paths import canonical_system_path


class StateDirectory:
    def __init__(self, fd: int):
        self.fd = fd

    @classmethod
    def open(cls, path: Path, *, create: bool = False) -> StateDirectory:
        absolute = canonical_system_path(path)
        fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in absolute.relative_to(absolute.anchor).parts:
                if create:
                    try:
                        os.mkdir(name, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return cls(fd)
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def name(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("expected one safe state filename")
        return value

    def child(self, name: str, *, create: bool = False) -> StateDirectory:
        name = self.name(name)
        if create:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        return StateDirectory(
            os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        )

    def exists(self, name: str) -> bool:
        try:
            info = os.stat(self.name(name), dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("state must be a regular unlinked file")
        return True

    def read(self, name: str, limit: int) -> bytes:
        fd = os.open(self.name(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("invalid or oversized state file")
            value = source.read(limit + 1)
            if len(value) > limit:
                raise ValueError("state exceeds byte budget")
            return value

    def write(self, name: str, value: bytes) -> None:
        name = self.name(name)
        temporary = ".state-" + uuid.uuid4().hex
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(value)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def acquire_owner(self) -> int:
        fd = os.open(
            "owner.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=self.fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("invalid owner lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise ValueError("validation owner is still active; recovery refused") from None

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> StateDirectory:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


@contextmanager
def open_run(store: Path, run_id: str):
    with StateDirectory.open(store) as root, root.child(run_id) as directory:
        yield directory
