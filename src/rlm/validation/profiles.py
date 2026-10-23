"""Small explicit profiles; project dependencies belong in the selected image."""

from __future__ import annotations

from rlm.validation.models import ValidationRequest


def runtime_commands(request: ValidationRequest) -> list[str]:
    commands = {"python": ["python"], "node": ["node", "npm"], "mixed": ["python", "node", "npm"]}[
        request.profile
    ].copy()
    if request.argv[0] == "pnpm":
        commands.append("pnpm")
    return commands


def probe_script(request: ValidationRequest) -> str:
    # Names are selected only from the constants above, never interpolated input.
    return " && ".join(
        f"{command} --version >> /tmp/runtime-versions 2>&1"
        for command in runtime_commands(request)
    )
