"""Private managed selection pinned once per SDK process; standalone use stays independent."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from rlm import __version__

_OPERATOR_MANIFEST = os.environ.get("SNIPARA_TOOLCHAIN_MANIFEST")
_OPERATOR_WORKSPACE = os.environ.get("SNIPARA_SANDBOX_VALIDATION_WORKSPACE")
_LOCK = threading.Lock()
_INITIALIZED = False
_PINNED: ManagedToolchain | None = None
_FAILURE: ManagedToolchainError | None = None


class ManagedToolchainError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManagedComponent:
    version: str
    command: Path
    sha256: str


@dataclass(frozen=True)
class ManagedToolchain:
    manifest_path: Path
    manifest_sha256: str
    root: Path
    components: Mapping[str, ManagedComponent]

    def environment(self, control_env: Mapping[str, str]) -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
        }
        env = {key: value for key, value in control_env.items() if key in allowed}
        env["SNIPARA_TOOLCHAIN_MANIFEST"] = str(self.manifest_path)
        if _OPERATOR_WORKSPACE:
            env["SNIPARA_SANDBOX_VALIDATION_WORKSPACE"] = _OPERATOR_WORKSPACE
        return env


def _directory(path: Path, private: bool) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & (0o077 if private else 0o022)
    ):
        raise ValueError("Managed directories must be private/owned and cannot be symlinks")


def _read(path: Path, maximum: int, *, private: bool, executable: bool = False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_size > maximum
            or info.st_mode & (0o077 if private else 0o022)
            or (executable and not info.st_mode & 0o111)
        ):
            raise ValueError("Managed file ownership, permissions or size are invalid")
        data = bytearray()
        while len(data) <= info.st_size:
            chunk = os.read(fd, info.st_size + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != info.st_size:
            raise ValueError("Managed file changed during reading")
        return bytes(data)
    finally:
        os.close(fd)


def _absolute(value: Any) -> Path:
    if (
        not isinstance(value, str)
        or not 1 < len(value) <= 4096
        or not value.startswith("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
    ):
        raise ValueError("Managed paths must be canonical and absolute")
    return Path(value)


def load_managed_toolchain(
    manifest_path: Path, *, runtime_command: Path | None = None, runtime_version: str = __version__
) -> ManagedToolchain:
    """Validate private control bytes; injectable runtime identity is for isolated qualification."""
    try:
        pointer = manifest_path.lstat()
        if not manifest_path.is_absolute() or pointer.st_uid != os.getuid():
            raise ValueError("Managed selection must be an owned absolute path")
        _directory(manifest_path.parent, True)
        selected = manifest_path.resolve(strict=True)
        _directory(selected.parent, True)
        raw = _read(selected, 65536, private=True)
        manifest = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != "snipara.toolchain.v1"
            or not isinstance(manifest.get("bundleId"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", manifest["bundleId"])
        ):
            raise ValueError("Unsupported managed manifest contract")
        contracts = manifest.get("contracts")
        if not isinstance(contracts, dict) or any(
            type(contracts.get(key)) is not int or contracts[key] != 1
            for key in ("taskReference", "validation")
        ):
            raise ValueError("Unsupported managed manifest contracts")
        root = _absolute(manifest.get("root"))
        if root.resolve(strict=True) != root or not selected.is_relative_to(root):
            raise ValueError("Managed manifest escaped its canonical bundle root")
        _directory(root, True)
        declared = manifest.get("components")
        capabilities = manifest.get("capabilities")
        if (
            not isinstance(declared, dict)
            or not isinstance(capabilities, list)
            or len(capabilities) > 32
            or any(
                not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item)
                for item in capabilities
            )
            or len(set(capabilities)) != len(capabilities)
        ):
            raise ValueError("Invalid managed component/capability declarations")
        components = {}
        for name, major, minimum_minor in (
            ("companion", 4, 2),
            ("orchestrator", 1, 16),
            ("sandbox", 3, 0),
        ):
            item = declared.get(name)
            if not isinstance(item, dict):
                raise ValueError("Missing managed component")
            version = item.get("version")
            match = (
                re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version) if isinstance(version, str) else None
            )
            if not match or int(match[1]) != major or int(match[2]) < minimum_minor:
                raise ManagedToolchainError(
                    "toolchain_version_incompatible",
                    "Repair local-work with a compatible stable component bundle",
                )
            command = _absolute(item.get("command"))
            digest = item.get("sha256")
            if (
                command == root
                or not command.is_relative_to(root)
                or command.resolve(strict=True) != command
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise ValueError("Managed component path/hash is invalid")
            current = command.parent
            while current != root:
                _directory(current, False)
                current = current.parent
            code = _read(command, 16 * 1024 * 1024, private=False, executable=True)
            if hashlib.sha256(code).hexdigest() != digest:
                raise ManagedToolchainError(
                    "toolchain_component_changed",
                    "A managed component changed; repair before execution",
                )
            components[name] = ManagedComponent(version, command, digest)
        # Keep the lexical virtualenv executable: resolving its Python symlink loses the venv sibling.
        actual = runtime_command or Path(sys.executable).parent / "snipara-sandbox"
        try:
            actual = actual.resolve(strict=True)
        except OSError as error:
            raise ManagedToolchainError(
                "toolchain_reconnect_required",
                "The loaded SDK has no matching console entrypoint; reconnect the native client",
            ) from error
        if (
            actual != components["sandbox"].command
            or runtime_version != components["sandbox"].version
        ):
            raise ManagedToolchainError(
                "toolchain_reconnect_required",
                "This SDK process differs from the selected bundle; reconnect the native client",
            )
        if (
            _read(selected, 65536, private=True) != raw
            or manifest_path.resolve(strict=True) != selected
        ):
            raise ValueError("Managed selection changed during resolution")
        return ManagedToolchain(
            selected, hashlib.sha256(raw).hexdigest(), root, MappingProxyType(components)
        )
    except ManagedToolchainError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise ManagedToolchainError(
            "toolchain_manifest_invalid",
            "Repair the private local-work selection; no fallback was attempted",
        ) from error


def selected_toolchain() -> ManagedToolchain | None:
    """Neither an active-pointer change nor later environment mutation changes this process."""
    global _INITIALIZED, _PINNED, _FAILURE
    with _LOCK:
        if not _INITIALIZED:
            try:
                _PINNED = (
                    load_managed_toolchain(Path(_OPERATOR_MANIFEST))
                    if _OPERATOR_MANIFEST is not None
                    else None
                )
            except ManagedToolchainError as error:
                _FAILURE = error
            _INITIALIZED = True
        if _FAILURE is not None:
            raise _FAILURE
        if _PINNED is not None:
            # Recheck pinned files, never the mutable active pointer, before each operation.
            try:
                _directory(_PINNED.root, True)
                if _PINNED.root.resolve(strict=True) != _PINNED.root:
                    raise ValueError("Pinned bundle root changed")
                raw = _read(_PINNED.manifest_path, 65536, private=True)
                if hashlib.sha256(raw).hexdigest() != _PINNED.manifest_sha256:
                    raise ValueError("Pinned manifest changed")
                for component in _PINNED.components.values():
                    if component.command.resolve(strict=True) != component.command:
                        raise ValueError("Pinned command escaped its bundle")
                    code = _read(
                        component.command, 16 * 1024 * 1024, private=False, executable=True
                    )
                    if hashlib.sha256(code).hexdigest() != component.sha256:
                        raise ValueError("Pinned command changed")
            except (OSError, ValueError) as error:
                raise ManagedToolchainError(
                    "toolchain_component_changed",
                    "The pinned local-work bundle changed; repair and reconnect before execution",
                ) from error
        return _PINNED


def managed_validation_scope(request_sha256: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", request_sha256):
        raise ValueError("Invalid canonical Sandbox request identity")
    return "snipara-validation:" + request_sha256
