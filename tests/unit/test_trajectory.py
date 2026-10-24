"""Tests for trajectory logging."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from rlm.core.types import ToolCall, ToolResult, TrajectoryEvent
from rlm.logging.trajectory import TrajectoryLogger

UTC = timezone.utc


@pytest.fixture
def log_dir(tmp_path):
    """Create a temporary log directory."""
    return tmp_path / "logs"


@pytest.fixture
def logger(log_dir):
    """Create a trajectory logger instance."""
    return TrajectoryLogger(log_dir=log_dir)


@pytest.fixture
def sample_event():
    """Create a sample trajectory event."""
    return TrajectoryEvent(
        trajectory_id=uuid4(),
        call_id=uuid4(),
        parent_call_id=None,
        depth=0,
        prompt="Test prompt",
        response="Test response",
        tool_calls=[],
        tool_results=[],
        repl_results=[],
        input_tokens=100,
        output_tokens=50,
        duration_ms=500,
        error=None,
        timestamp=datetime.now(UTC),
    )


class TestTrajectoryLoggerInit:
    """Tests for TrajectoryLogger initialization."""

    def test_creates_log_dir(self, log_dir):
        """Should create log directory if it doesn't exist."""
        assert not log_dir.exists()
        TrajectoryLogger(log_dir=log_dir)
        assert log_dir.exists()

    def test_uses_default_log_dir(self, tmp_path, monkeypatch):
        """Should use ./logs as default directory."""
        monkeypatch.chdir(tmp_path)
        logger = TrajectoryLogger()
        assert logger.log_dir == Path("./logs")

    def test_verbose_flag(self, log_dir):
        """Should store verbose flag."""
        logger = TrajectoryLogger(log_dir=log_dir, verbose=True)
        assert logger.verbose is True


class TestLogEvent:
    """Tests for log_event method."""

    def test_logs_event_to_file(self, logger, sample_event):
        """Should write event to JSONL file."""
        logger.log_event(sample_event)

        log_path = logger._get_log_path(sample_event.trajectory_id)
        assert log_path.exists()

        with open(log_path) as f:
            data = json.loads(f.readline())

        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"

    def test_appends_events(self, logger):
        """Should append multiple events to same file."""
        trajectory_id = uuid4()

        event1 = TrajectoryEvent(
            trajectory_id=trajectory_id,
            call_id=uuid4(),
            parent_call_id=None,
            depth=0,
            prompt="First",
            response="Response 1",
            tool_calls=[],
            tool_results=[],
            repl_results=[],
            input_tokens=10,
            output_tokens=5,
            duration_ms=100,
        )
        event2 = TrajectoryEvent(
            trajectory_id=trajectory_id,
            call_id=uuid4(),
            parent_call_id=None,
            depth=1,
            prompt="Second",
            response="Response 2",
            tool_calls=[],
            tool_results=[],
            repl_results=[],
            input_tokens=20,
            output_tokens=10,
            duration_ms=200,
        )

        logger.log_event(event1)
        logger.log_event(event2)

        log_path = logger._get_log_path(trajectory_id)
        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 2


class TestLogTrajectory:
    """Tests for log_trajectory method."""

    def test_logs_complete_trajectory(self, logger):
        """Should write metadata and all events."""
        trajectory_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=trajectory_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=i,
                prompt=f"Prompt {i}",
                response=f"Response {i}",
                tool_calls=[],
                tool_results=[],
                repl_results=[],
                input_tokens=100,
                output_tokens=50,
                duration_ms=500,
            )
            for i in range(3)
        ]

        log_path = logger.log_trajectory(trajectory_id, events)

        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()

        # Metadata + 3 events
        assert len(lines) == 4

        # Check metadata
        metadata = json.loads(lines[0])
        assert metadata["_type"] == "trajectory_metadata"
        assert metadata["event_count"] == 3
        assert metadata["total_tokens"] == 450  # 3 * (100 + 50)
        assert metadata["total_duration_ms"] == 1500  # 3 * 500

    def test_returns_log_path(self, logger):
        """Should return path to log file."""
        trajectory_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=trajectory_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=0,
                prompt="Test",
                response="Response",
                tool_calls=[],
                tool_results=[],
                repl_results=[],
                input_tokens=10,
                output_tokens=5,
                duration_ms=100,
            )
        ]

        result = logger.log_trajectory(trajectory_id, events)

        assert isinstance(result, Path)
        assert result.suffix == ".jsonl"


class TestLoadTrajectory:
    """Tests for load_trajectory method."""

    def test_loads_logged_trajectory(self, logger):
        """Should load previously logged events."""
        trajectory_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=trajectory_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=0,
                prompt="Test prompt",
                response="Test response",
                tool_calls=[ToolCall(id="tc1", name="test_tool", arguments={"arg": "val"})],
                tool_results=[ToolResult(tool_call_id="tc1", content="result", is_error=False)],
                repl_results=[],
                input_tokens=100,
                output_tokens=50,
                duration_ms=500,
            )
        ]

        logger.log_trajectory(trajectory_id, events)
        loaded = logger.load_trajectory(str(trajectory_id))

        assert len(loaded) == 1
        assert loaded[0].prompt == "Test prompt"
        assert loaded[0].response == "Test response"
        assert len(loaded[0].tool_calls) == 1
        assert loaded[0].tool_calls[0].name == "test_tool"

    def test_returns_empty_for_nonexistent(self, logger):
        """Should return empty list for non-existent trajectory."""
        result = logger.load_trajectory("nonexistent-id")
        assert result == []

    def test_skips_empty_lines(self, logger, log_dir):
        """Should skip empty lines in log file."""
        trajectory_id = str(uuid4())
        log_path = log_dir / f"{trajectory_id}.jsonl"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Write file with empty lines
        with open(log_path, "w") as f:
            f.write('{"_type": "trajectory_metadata"}\n')
            f.write("\n")
            f.write(
                json.dumps(
                    {
                        "trajectory_id": trajectory_id,
                        "call_id": str(uuid4()),
                        "depth": 0,
                        "prompt": "Test",
                        "response": "Response",
                        "tool_calls": [],
                        "tool_results": [],
                        "repl_results": [],
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "duration_ms": 100,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n"
            )

        events = logger.load_trajectory(trajectory_id)
        assert len(events) == 1


class TestListRecent:
    """Tests for list_recent method."""

    def test_lists_recent_trajectories(self, logger):
        """Should list trajectories sorted by modification time."""
        # Create multiple trajectories
        for i in range(3):
            trajectory_id = uuid4()
            events = [
                TrajectoryEvent(
                    trajectory_id=trajectory_id,
                    call_id=uuid4(),
                    parent_call_id=None,
                    depth=0,
                    prompt=f"Test {i}",
                    response=f"Response {i}",
                    tool_calls=[],
                    tool_results=[],
                    repl_results=[],
                    input_tokens=100 * (i + 1),
                    output_tokens=50 * (i + 1),
                    duration_ms=500 * (i + 1),
                )
            ]
            logger.log_trajectory(trajectory_id, events)
            time.sleep(0.01)  # Ensure different mtime

        recent = logger.list_recent(limit=10)

        assert len(recent) == 3
        # Should have all expected fields
        assert "id" in recent[0]
        assert "timestamp" in recent[0]
        assert "calls" in recent[0]
        assert "tokens" in recent[0]

    def test_respects_limit(self, logger):
        """Should respect the limit parameter."""
        for _i in range(5):
            trajectory_id = uuid4()
            events = [
                TrajectoryEvent(
                    trajectory_id=trajectory_id,
                    call_id=uuid4(),
                    parent_call_id=None,
                    depth=0,
                    prompt="Test",
                    response="Response",
                    tool_calls=[],
                    tool_results=[],
                    repl_results=[],
                    input_tokens=10,
                    output_tokens=5,
                    duration_ms=100,
                )
            ]
            logger.log_trajectory(trajectory_id, events)

        recent = logger.list_recent(limit=2)

        assert len(recent) == 2

    def test_returns_empty_when_no_logs(self, logger):
        """Should return empty list when no logs exist."""
        recent = logger.list_recent()
        assert recent == []


class TestDeleteTrajectory:
    """Tests for delete_trajectory method."""

    def test_deletes_existing_trajectory(self, logger):
        """Should delete trajectory file and return True."""
        trajectory_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=trajectory_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=0,
                prompt="Test",
                response="Response",
                tool_calls=[],
                tool_results=[],
                repl_results=[],
                input_tokens=10,
                output_tokens=5,
                duration_ms=100,
            )
        ]
        logger.log_trajectory(trajectory_id, events)

        result = logger.delete_trajectory(str(trajectory_id))

        assert result is True
        assert not logger._get_log_path(trajectory_id).exists()

    def test_returns_false_for_nonexistent(self, logger):
        """Should return False for non-existent trajectory."""
        result = logger.delete_trajectory("nonexistent-id")
        assert result is False


class TestCleanupOld:
    """Tests for cleanup_old method."""

    def test_deletes_old_files(self, logger, log_dir):
        """Should delete files older than max_age_days."""
        # Create an old file
        old_file = log_dir / "old-trajectory.jsonl"
        old_file.write_text('{"_type": "test"}\n')

        # Set modification time to 10 days ago
        old_time = time.time() - (10 * 24 * 60 * 60)
        import os

        os.utime(old_file, (old_time, old_time))

        # Create a recent file
        recent_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=recent_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=0,
                prompt="Test",
                response="Response",
                tool_calls=[],
                tool_results=[],
                repl_results=[],
                input_tokens=10,
                output_tokens=5,
                duration_ms=100,
            )
        ]
        logger.log_trajectory(recent_id, events)

        deleted = logger.cleanup_old(max_age_days=7)

        assert deleted == 1
        assert not old_file.exists()
        assert logger._get_log_path(recent_id).exists()

    def test_returns_zero_when_no_old_files(self, logger):
        """Should return 0 when no old files to delete."""
        # Create recent file
        trajectory_id = uuid4()
        events = [
            TrajectoryEvent(
                trajectory_id=trajectory_id,
                call_id=uuid4(),
                parent_call_id=None,
                depth=0,
                prompt="Test",
                response="Response",
                tool_calls=[],
                tool_results=[],
                repl_results=[],
                input_tokens=10,
                output_tokens=5,
                duration_ms=100,
            )
        ]
        logger.log_trajectory(trajectory_id, events)

        deleted = logger.cleanup_old(max_age_days=7)

        assert deleted == 0
