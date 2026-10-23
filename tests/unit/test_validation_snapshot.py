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
