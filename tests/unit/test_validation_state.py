"""State identity must survive path replacement without external writes."""

from rlm.validation.state import StateDirectory


def test_pinned_state_operations_ignore_replaced_ancestors(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    state = root / ".snipara"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with StateDirectory.open(state, create=False) as directory:
        state.rename(root / "moved-state")
        state.symlink_to(outside, target_is_directory=True)
        directory.write("receipt.json", b"owned")
        assert directory.read("receipt.json", 100) == b"owned"
    assert list(outside.iterdir()) == []
    assert (root / "moved-state/receipt.json").read_bytes() == b"owned"
