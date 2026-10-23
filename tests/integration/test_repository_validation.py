"""Real Docker acceptance controls; opt in explicitly to require a daemon."""

import os
from pathlib import Path

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
        for owned in (receipt, failed, timeout):
            assert (
                client.containers.list(
                    all=True, filters={"label": "com.snipara.sandbox.validation=" + owned["run_id"]}
                )
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


def test_real_cancel_log_budget_and_filtered_credentials(tmp_path):
    import concurrent.futures
    import json
    import time

    from rlm.validation.runner import cancel

    project = tmp_path / "project"
    project.mkdir()
    (project / ".env.local").write_text("TOKEN=should-never-appear")
    (project / "check.py").write_text(
        "from pathlib import Path\nassert not Path('.env.local').exists()\nprint('x'*100000)\n"
    )
    store = tmp_path / "runs"
    request = ValidationRequest(
        workspace=str(project), argv=["python", "check.py"], image="python:3.11-slim"
    )
    result = run(request, store)
    assert result["status"] == "passed" and result["logs_truncated"]
    assert (store / result["run_id"] / "output.log").stat().st_size <= 65536
    (project / "check.py").write_text(
        'import subprocess,time\nsubprocess.Popen(["sleep","60"])\ntime.sleep(60)'
    )
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run, request, store)
        deadline = time.monotonic() + 15
        active = None
        while time.monotonic() < deadline:
            for path in store.glob("*/receipt.json"):
                receipt = json.loads(path.read_text())
                if receipt["status"] == "running":
                    active = receipt["run_id"]
            if active:
                break
            time.sleep(0.05)
        assert active
        assert cancel(store, active)["cancel_requested"]
        result = future.result(timeout=15)
    assert result["status"] == "cancelled" and result["cleanup_complete"]


def test_real_fifo_collection_deadline_and_image_volume_refusal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text(
        "import os\nos.unlink('/tmp/validation-output')\nos.mkfifo('/tmp/validation-output')\n"
    )
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "check.py"],
        image="python:3.11-slim",
        timeout_seconds=2,
    )
    result = run(request, tmp_path / "runs")
    assert result["status"] in {"timeout", "collection_error"} and result["cleanup_complete"], (
        result
    )
    assert result["duration_ms"] < 15000
    volume_image = os.environ.get("SNIPARA_VOLUME_IMAGE")
    if volume_image:
        refused = run(request.model_copy(update={"image": volume_image}), tmp_path / "runs")
        assert refused["status"] == "error" and "image-declared volumes" in refused["error"]
        assert "container_id" not in refused


def test_real_nonroot_offline_and_disk_budget(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text("""import errno,os,socket
assert os.getuid() == 65534
try:
    socket.create_connection(('1.1.1.1',80),timeout=0.5)
except OSError:
    pass
else:
    raise AssertionError('network unexpectedly available')
try:
    with open('large.bin','wb') as target:
        for _ in range(40):target.write(b'x'*1024*1024)
except OSError as error:
    assert error.errno == errno.ENOSPC
else:
    raise AssertionError('disk quota not enforced')
""")
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "check.py"],
        image="python:3.11-slim",
        disk_mb=16,
        memory_mb=64,
        pids=16,
    )
    result = run(request, tmp_path / "runs")
    assert result["status"] == "passed", result
    assert not (project / "large.bin").exists()


def test_real_owner_process_crash_recovery(tmp_path):
    import json
    import subprocess
    import sys
    import time

    from rlm.validation.runner import recover

    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text("import time\ntime.sleep(60)")
    request = ValidationRequest(
        workspace=str(project), argv=["python", "check.py"], image="python:3.11-slim"
    )
    config = tmp_path / "request.json"
    config.write_text(request.model_dump_json())
    store = tmp_path / "runs"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "rlm.cli.main",
            "validation",
            "run",
            "--request",
            str(config),
            "--store",
            str(store),
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    receipt = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for path in store.glob("*/receipt.json"):
                value = json.loads(path.read_text())
                if value["status"] == "running":
                    receipt = value
            if receipt:
                break
            time.sleep(0.1)
        assert receipt
        process.kill()
        process.wait(timeout=5)
        result = recover(store, receipt["run_id"])
        assert result["status"] == "interrupted" and result["cleanup_complete"]
        assert not Path(receipt["snapshot_path"]).exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "profile,image,program,argv",
    [
        (
            "python",
            "python:3.11-slim",
            "from pathlib import Path\nPath('escape').symlink_to('/etc', target_is_directory=True)\n",
            ["python", "check.py"],
        ),
        (
            "node",
            "node:24-bookworm-slim",
            "require('node:fs').symlinkSync('/etc','escape')",
            ["node", "check.js"],
        ),
    ],
)
def test_real_artifact_links_and_missing_outputs_are_refused(
    tmp_path, profile, image, program, argv
):
    project = tmp_path / "project"
    project.mkdir()
    (project / argv[1]).write_text(program)
    request = ValidationRequest(
        workspace=str(project), argv=argv, image=image, profile=profile, artifacts=["escape/passwd"]
    )
    escaped = run(request, tmp_path / "runs")
    assert escaped["status"] == "artifact_error" and escaped["artifacts"] == [], escaped
    missing = run(request.model_copy(update={"artifacts": ["missing.txt"]}), tmp_path / "runs")
    assert missing["status"] == "artifact_error" and missing["artifacts"] == [], missing


def test_real_missing_log_is_not_accepted_and_empty_cwd_is_preserved(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "empty").mkdir()
    (project / "check.py").write_text("import os\nos.unlink('/tmp/validation-output')")
    request = ValidationRequest(
        workspace=str(project), argv=["python", "check.py"], image="python:3.11-slim"
    )
    result = run(request, tmp_path / "runs")
    assert result["status"] == "collection_error", result
    empty = run(
        request.model_copy(update={"cwd": "empty", "argv": ["python", "--version"]}),
        tmp_path / "runs",
    )
    assert empty["status"] == "passed", empty
