"""Real Docker: the Sandbox CLI coordinates scoped work without any Orchestrator."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rlm.validation.runner import docker_client

pytestmark = pytest.mark.skipif(
    os.environ.get("SNIPARA_DOCKER_TESTS") != "1",
    reason="requires explicit real Docker qualification",
)
SRC = str(Path(__file__).resolve().parents[2] / "src")
ENTRY = "import sys; sys.argv[0]='snipara-sandbox'; from rlm.cli.main import app; app()"


def test_real_scoped_cli_executes_once_then_reuses_verified_receipt(tmp_path):
    client = docker_client()
    try:
        image = client.images.get(
            os.environ.get("SNIPARA_DOCKER_TEST_IMAGE", "python:3.11-slim")
        ).id
    except Exception as error:  # pragma: no cover - depends on the local image cache
        pytest.skip(f"prepared test image unavailable: {error}")
    finally:
        client.close()
    (tmp_path / ".git").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text("print('scoped ok')\n")
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps({"workspace": str(project), "argv": ["python", "check.py"], "image": image})
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = subprocess.run(["which", "docker"], capture_output=True, text=True).stdout.strip()
    if docker:
        (bin_dir / "docker").symlink_to(docker)  # Docker only; no Orchestrator on PATH.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "TMPDIR", "LANG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"}
    }
    env.update(PATH=str(bin_dir), PYTHONPATH=SRC)

    def scoped(*extra):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                ENTRY,
                "validation",
                "run",
                "--request",
                str(request),
                "--scope",
                "workflow/lot",
                "--store",
                str(tmp_path / ".snipara/validation"),
                *extra,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        return result.returncode, json.loads(result.stdout)

    code, first = scoped()
    assert code == 0, first
    assert first["work_reuse"]["state"] == "executed" and first["cleanup_complete"] is True
    code, second = scoped()
    assert code == 0 and second["work_reuse"]["state"] == "reused"
    assert second["run_id"] == first["run_id"]
    (project / "check.py").write_text("print('changed')\n")
    code, third = scoped()
    assert code == 0 and third["work_reuse"]["state"] == "executed"
    assert third["run_id"] != first["run_id"]
    code, fresh = scoped("--no-reuse")
    assert code == 0 and fresh["work_reuse"]["state"] == "executed"
