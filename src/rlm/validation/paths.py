"""Reject host-side symlink indirection before reading or creating control state."""

from pathlib import Path


def secure_path(path: Path) -> Path:
    absolute = path.absolute()
    for part in [*reversed(absolute.parents), absolute]:
        if part.is_symlink():
            raise ValueError("symlink ancestor refused for validation state")
    return absolute
