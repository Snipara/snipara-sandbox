"""Reject host-side symlink indirection before reading or creating control state."""

import os
from pathlib import Path


def secure_path(path: Path) -> Path:
    absolute = canonical_system_path(path)
    for part in [*reversed(absolute.parents), absolute]:
        if part.is_symlink():
            raise ValueError("symlink ancestor refused for validation state")
    return absolute


def canonical_system_path(path: Path) -> Path:
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise ValueError("state path traversal refused")
    # Only root-owned conventional OS aliases are normalized. Project symlinks
    # remain subject to descriptor-relative NOFOLLOW traversal.
    for prefix in (Path("/tmp"), Path("/var")):
        if absolute == prefix or prefix in absolute.parents:
            return Path(os.path.realpath(prefix)) / absolute.relative_to(prefix)
    return absolute
