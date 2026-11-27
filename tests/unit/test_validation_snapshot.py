"""Exact source capture and immutable input boundaries."""

import hashlib

import pytest

from rlm.validation.snapshot import capture


def test_snapshot_tracks_dirty_sources_and_excludes_secrets(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.py").write_text("original")
    (project / ".env.local").write_text("private")
    (project / ".git").mkdir()
    (project / ".git/config").write_text("private")
    destination = tmp_path / "copy"
    destination.mkdir()
    before = capture(project, destination)
    assert [item["path"] for item in before["files"]] == ["source.py"]
    assert not (destination / ".env.local").exists()
    assert (destination / "source.py").read_text() == "original"
    assert before["files"][0]["sha256"] == hashlib.sha256(b"original").hexdigest()
    (project / "source.py").write_text("uncommitted")
    assert capture(project)["sha256"] != before["sha256"]


@pytest.mark.parametrize("directory", [False, True])
def test_symlinks_never_escape_snapshot(tmp_path, directory):
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "secret"
    if directory:
        target.mkdir()
    else:
        target.write_text("private")
    (project / "escape").symlink_to(target, target_is_directory=directory)
    with pytest.raises(ValueError):
        capture(project)


def test_mode_and_exclusion_changes_are_identity_changes(tmp_path):
    source = tmp_path / "program"
    source.write_text("same")
    original = capture(tmp_path)["sha256"]
    source.chmod(0o755)
    assert capture(tmp_path)["sha256"] != original


def test_directory_entry_budget_bounds_metadata(tmp_path, monkeypatch):
    import rlm.validation.snapshot as snapshots

    monkeypatch.setattr(snapshots, "MAX_ENTRIES", 2)
    for index in range(3):
        (tmp_path / str(index)).write_text("small")
    with pytest.raises(ValueError, match="entry/path"):
        capture(tmp_path)


def test_unreadable_subtree_is_fatal_not_a_reduced_snapshot(tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("root bypasses filesystem read permissions")
    hidden = tmp_path / "required-tests"
    hidden.mkdir()
    (hidden / "test.py").write_text("required")
    hidden.chmod(0)
    try:
        with pytest.raises(PermissionError):
            capture(tmp_path)
    finally:
        hidden.chmod(0o700)


def test_empty_directories_and_umask_are_part_of_normalized_snapshot(tmp_path):
    import os

    project = tmp_path / "project"
    project.mkdir()
    (project / "empty").mkdir()
    original = capture(project)
    assert "empty" in [item["path"] for item in original["directories"]]
    target = tmp_path / "copy"
    previous = os.umask(0o077)
    try:
        capture(project, target)
    finally:
        os.umask(previous)
    assert (target / "empty").is_dir()
    assert (target / "empty").stat().st_mode & 0o777 == 0o755
    (project / "empty").rmdir()
    assert capture(project)["sha256"] != original["sha256"]


def test_descriptor_capture_cannot_follow_replaced_ancestor(tmp_path):
    import os

    from rlm.validation.snapshot import pin_directory

    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    (child / "source").write_text("authorized")
    fd = pin_directory(child, root=root)
    child.rename(tmp_path / "moved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_text("not authorized")
    child.symlink_to(outside, target_is_directory=True)
    try:
        assert [item["path"] for item in capture(child, workspace_fd=fd)["files"]] == ["source"]
    finally:
        os.close(fd)


def test_capture_worker_honors_cancel_and_deadline(tmp_path):
    import time

    from rlm.validation.snapshot import SnapshotCancelledError, capture_bounded

    with pytest.raises(TimeoutError):
        capture_bounded(tmp_path, deadline=time.monotonic() - 1)
    with pytest.raises(SnapshotCancelledError):
        capture_bounded(tmp_path, deadline=time.monotonic() + 10, cancelled=lambda: True)


def test_slow_capture_helper_is_killed_at_deadline(tmp_path, monkeypatch):
    import time

    import rlm.validation.snapshot as snapshots

    helper = tmp_path / "owned-slow-helper.py"
    helper.write_text(
        "import time\ndef _watch_owner(*args): pass\ndef capture(*args,**kwargs):\n time.sleep(30)\n return {}\n"
    )
    monkeypatch.setattr(snapshots, "__file__", str(helper))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        snapshots.capture_bounded(tmp_path, deadline=started + 0.2)
    assert time.monotonic() - started < 3


def test_source_hardlinks_and_recognizable_credentials_are_refused(tmp_path):
    import os

    outside = tmp_path / "outside"
    outside.write_text("synthetic-private")
    project = tmp_path / "project"
    project.mkdir()
    os.link(outside, project / "ordinary.txt")
    with pytest.raises(ValueError):
        capture(project)
    (project / "ordinary.txt").unlink()
    for folder in [".docker", ".kube"]:
        (project / folder).mkdir()
        (project / folder / "config.json").write_text("private")
    (project / "secrets.yml").write_text("private")
    assert capture(project)["files"] == []


def test_capture_helper_exits_when_owner_lifeline_closes(tmp_path):
    import os
    import subprocess
    import sys

    import rlm.validation.snapshot as snapshots

    read_fd, write_fd = os.pipe()
    script = "import runpy,sys,time; m=runpy.run_path(sys.argv[1]); m['_watch_owner'](int(sys.argv[2]),time.monotonic()+30); time.sleep(30)"
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script, snapshots.__file__, str(read_fd)], pass_fds=(read_fd,)
    )
    os.close(read_fd)
    os.close(write_fd)
    try:
        assert process.wait(timeout=3) == 125
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_recovery_refuses_live_snapshot_writer(tmp_path):
    import fcntl
    import os

    from rlm.validation.snapshot import remove_recovered_snapshot

    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(ValueError, match="still active"):
            remove_recovered_snapshot(tmp_path)
        assert tmp_path.exists()
    finally:
        os.close(fd)


def test_source_directory_acquisition_is_bounded_and_transfers_exact_fd(tmp_path, monkeypatch):
    import os
    import time

    import rlm.validation.snapshot as snapshots

    root = tmp_path / "root"
    root.mkdir()
    fd = snapshots.pin_directory_bounded(root, root=tmp_path)
    try:
        assert os.fstat(fd).st_ino == root.stat().st_ino
    finally:
        os.close(fd)
    helper = tmp_path / "slow-directory.py"
    helper.write_text(
        "import time\ndef _watch_owner(*a): pass\ndef pin_directory(*a,**k): time.sleep(30)\n"
    )
    monkeypatch.setattr(snapshots, "__file__", str(helper))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        snapshots.pin_directory_bounded(root, root=tmp_path, timeout=0.2)
    assert time.monotonic() - started < 3


def test_source_ancestor_symlink_is_never_resolved_into_authority(tmp_path):
    import time

    from rlm.validation.snapshot import capture_bounded

    outside = tmp_path / "outside"
    child = outside / "child"
    child.mkdir(parents=True)
    (child / "private").write_text("synthetic")
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        capture_bounded(alias / "child", deadline=time.monotonic() + 2)


def test_request_exclude_leaves_paths_out_and_reports_them(tmp_path):
    from rlm.validation.snapshot import capture

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)\n")
    (tmp_path / "assets" / "big").mkdir(parents=True)
    (tmp_path / "assets" / "big" / "blob.bin").write_bytes(b"x" * 1024)
    (tmp_path / "setup.exe").write_bytes(b"y" * 1024)
    full = capture(tmp_path)
    slim = capture(tmp_path, exclude=("assets/big", "setup.exe"))
    assert [item["path"] for item in slim["files"]] == ["src/main.py"]
    assert slim["excluded"] == ["assets/big", "setup.exe"]
    assert slim["sha256"] != full["sha256"] and slim["bytes"] == 9
    # A name match elsewhere in the tree is not excluded: entries are whole relative paths.
    (tmp_path / "src" / "setup.exe").write_bytes(b"z")
    assert "src/setup.exe" in [
        item["path"] for item in capture(tmp_path, exclude=("setup.exe",))["files"]
    ]


def test_byte_budget_error_names_the_path_and_the_exclude_field(tmp_path, monkeypatch):
    import pytest

    from rlm.validation import snapshot

    monkeypatch.setattr(snapshot, "MAX_BYTES", 2 * 1024 * 1024)
    (tmp_path / "video.bin").write_bytes(b"0" * (3 * 1024 * 1024))
    with pytest.raises(ValueError, match=r"2 MB byte budget at video\.bin; .*exclude field"):
        snapshot.capture(tmp_path)
    assert snapshot.capture(tmp_path, exclude=("video.bin",))["files"] == []


def test_request_exclude_is_validated_and_keeps_old_hashes():
    import pytest

    from rlm.validation.models import ValidationRequest

    base = {"workspace": "/w", "argv": ["true"], "image": "sha256:" + "a" * 64}
    plain = ValidationRequest.model_validate(base)
    assert "exclude" not in plain.identity_payload()
    slim = ValidationRequest.model_validate({**base, "exclude": ["b/c", "a"]})
    assert slim.exclude == ["a", "b/c"] and slim.identity_payload()["exclude"] == ["a", "b/c"]
    for bad in (["../x"], ["/abs"], ["."], ["a", "a"]):
        with pytest.raises(ValueError):
            ValidationRequest.model_validate({**base, "exclude": bad})


def test_bounded_capture_reports_the_reason_not_a_traceback(tmp_path):
    import time

    import pytest

    from rlm.validation import snapshot

    (tmp_path / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError) as caught:
        snapshot.capture_bounded(tmp_path, deadline=time.monotonic() + 30)
    assert str(caught.value) == "source link or special file refused: link"
    assert snapshot.capture_bounded(tmp_path, deadline=time.monotonic() + 30, exclude=["link"])[
        "excluded"
    ] == ["link"]
