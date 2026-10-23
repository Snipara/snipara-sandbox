"""Real Docker acceptance controls; opt in explicitly to require a daemon."""

import os

import pytest

from rlm.validation.models import ValidationRequest
from rlm.validation.runner import docker_client, run, verify

pytestmark = pytest.mark.skipif(
    os.environ.get("SNIPARA_DOCKER_TESTS") != "1",
    reason="requires explicit real Docker qualification",
)


def test_real_python_write_failure_timeout_and_no_source_mutation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text(
        "from pathlib import Path\nPath('generated.txt').write_text('ok')\nprint('passed')\n"
    )
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "check.py"],
        image="python:3.11-slim",
        artifacts=["generated.txt"],
    )
    store = tmp_path / "runs"
    client = docker_client()
    try:
        receipt = run(request, store, client=client)
        assert receipt["status"] == "passed", receipt
        assert receipt["cleanup_complete"] is True
        assert not (project / "generated.txt").exists()
        assert verify(request, store, receipt["run_id"], client=client)["valid"]
        (project / "check.py").write_text("raise SystemExit(7)")
        assert not verify(request, store, receipt["run_id"], client=client)["valid"]
        failed = run(request.model_copy(update={"artifacts": []}), store, client=client)
        assert failed["status"] == "failed" and failed["exit_code"] == 7
        (project / "check.py").write_text("import time\ntime.sleep(60)")
        timeout = run(
            request.model_copy(update={"timeout_seconds": 1, "artifacts": []}), store, client=client
        )
        assert timeout["status"] == "timeout" and timeout["cleanup_complete"]
        assert (
            client.containers.list(all=True, filters={"label": "com.snipara.sandbox.validation"})
            == []
        )
    finally:
        client.close()


def test_real_node_and_incompatible_profile(tmp_path):
    project = tmp_path / "node-project"
    project.mkdir()
    (project / "check.js").write_text(
        "require('node:fs').writeFileSync('built.txt','node works'); console.log(process.version)"
    )
    store = tmp_path / "runs"
    request = ValidationRequest(
        workspace=str(project),
        argv=["node", "check.js"],
        image="node:24-bookworm-slim",
        profile="node",
        artifacts=["built.txt"],
    )
    passed = run(request, store)
    assert passed["status"] == "passed", passed
    assert passed["runtime_versions"].strip()
    assert not (project / "built.txt").exists()
    incompatible = run(request.model_copy(update={"profile": "mixed", "artifacts": []}), store)
    assert incompatible["status"] == "environment_incompatible", incompatible
    (project / "check.js").write_text("process.exit(9)")
    failed = run(request.model_copy(update={"artifacts": []}), store)
    assert failed["status"] == "failed" and failed["exit_code"] == 9


def test_real_mixed_profile(tmp_path):
    image = os.environ.get("SNIPARA_MIXED_IMAGE")
    if not image:
        pytest.skip("provide explicit mixed image to qualify this profile")
    project = tmp_path / "mixed"
    project.mkdir()
    (project / "check.py").write_text(
        "import subprocess\nfrom pathlib import Path\nsubprocess.run(['node','-e',\"require('node:fs').writeFileSync('mixed.txt','ok')\"],check=True)\nassert Path('mixed.txt').read_text() == 'ok'\n"
    )
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "check.py"],
        image=image,
        profile="mixed",
        artifacts=["mixed.txt"],
    )
    result = run(request, tmp_path / "runs")
    assert result["status"] == "passed", result
    assert not (project / "mixed.txt").exists()
