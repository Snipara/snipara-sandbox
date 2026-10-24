"""Small explicit profiles; project dependencies belong in the selected image."""

from __future__ import annotations

import json
import shlex

from rlm.validation.models import ValidationRequest


def runtime_commands(request: ValidationRequest) -> list[str]:
    commands = {"python": ["python"], "node": ["node", "npm"], "mixed": ["python", "node", "npm"]}[
        request.profile
    ].copy()
    if request.argv[0] == "pnpm":
        commands.append("pnpm")
    if request.requirements.python_modules and "python" not in commands:
        commands.append("python")
    if request.requirements.node_modules and "node" not in commands:
        commands.append("node")
    return commands


def probe_script(request: ValidationRequest) -> str:
    checks = [f"{command} --version" for command in runtime_commands(request)]
    for command in request.requirements.commands:
        checks.append(
            f"command -v {shlex.quote(command)} >/dev/null 2>&1 || "
            f"{{ printf '%s\\n' {shlex.quote('missing command: ' + command)}; exit 1; }}"
        )
    if request.requirements.python_modules:
        code = (
            "import importlib; "
            f"modules={request.requirements.python_modules!r}; "
            "[(importlib.import_module(name), print('python module ready:', name)) for name in modules]"
        )
        checks.append("python -c " + shlex.quote(code))
    if request.requirements.node_modules:
        code = (
            f"const modules={json.dumps(request.requirements.node_modules)}; "
            "for (const name of modules) { require.resolve(name); console.log('node module ready:', name); }"
        )
        checks.append("node -e " + shlex.quote(code))
    return " && ".join(f"({check})" for check in checks)
