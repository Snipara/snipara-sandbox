"""Trajectory logging for RLM executions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from rlm.core.types import TrajectoryEvent

logger = structlog.get_logger()


UTC = timezone.utc


class TrajectoryLogger:
    """Logger for RLM execution trajectories.

    Writes trajectory events to JSONL files for debugging and analysis.

    Example:
        ```python
        logger = TrajectoryLogger(log_dir=Path("./logs"))
        logger.log_trajectory(trajectory_id, events)

        # Later: load and inspect
        events = logger.load_trajectory(str(trajectory_id))
        ```
    """

    def __init__(
        self,
        log_dir: Path | None = None,
        verbose: bool = False,
    ):
        """Initialize the trajectory logger.

        Args:
            log_dir: Directory to write log files (default: ./logs)
            verbose: Enable verbose console logging
        """
        self.log_dir = log_dir or Path("./logs")
        self.verbose = verbose

        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_path(self, trajectory_id: UUID | str) -> Path:
        """Get the log file path for a trajectory."""
        return self.log_dir / f"{trajectory_id}.jsonl"

    def log_event(self, event: TrajectoryEvent) -> None:
        """Log a single trajectory event.

        Args:
            event: TrajectoryEvent to log
        """
        log_path = self._get_log_path(event.trajectory_id)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

        if self.verbose:
            logger.info(
                "Event logged",
                trajectory_id=str(event.trajectory_id),
                call_id=str(event.call_id),
                depth=event.depth,
            )

    def log_trajectory(
        self,
        trajectory_id: UUID,
        events: list[TrajectoryEvent],
    ) -> Path:
        """Log a complete trajectory.

        Args:
            trajectory_id: Unique ID for the trajectory
            events: List of events in the trajectory

        Returns:
            Path to the log file
        """
        log_path = self._get_log_path(trajectory_id)

        with open(log_path, "w", encoding="utf-8") as f:
            # Write metadata header
            metadata = {
                "_type": "trajectory_metadata",
                "trajectory_id": str(trajectory_id),
                "timestamp": datetime.now(UTC).isoformat(),
                "event_count": len(events),
                "total_tokens": sum(e.input_tokens + e.output_tokens for e in events),
                "total_duration_ms": sum(e.duration_ms for e in events),
            }
            f.write(json.dumps(metadata) + "\n")

            # Write events
            for event in events:
                f.write(json.dumps(event.to_dict()) + "\n")

        if self.verbose:
            logger.info(
                "Trajectory logged",
                trajectory_id=str(trajectory_id),
                event_count=len(events),
                path=str(log_path),
            )

        return log_path

    def load_trajectory(self, trajectory_id: str) -> list[TrajectoryEvent]:
        """Load a trajectory from disk.

        Args:
            trajectory_id: ID of the trajectory to load

        Returns:
            List of trajectory events (empty if not found)
        """
        log_path = self._get_log_path(trajectory_id)

        if not log_path.exists():
            return []

        events: list[TrajectoryEvent] = []

        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                # Skip metadata lines
                if data.get("_type") == "trajectory_metadata":
                    continue

                # Parse event
                events.append(self._parse_event(data))

        return events

    def _parse_event(self, data: dict[str, Any]) -> TrajectoryEvent:
        """Parse a trajectory event from JSON data."""
        from rlm.core.types import REPLResult, ToolCall, ToolResult

        return TrajectoryEvent(
            trajectory_id=UUID(data["trajectory_id"]),
            call_id=UUID(data["call_id"]),
            parent_call_id=UUID(data["parent_call_id"]) if data.get("parent_call_id") else None,
            depth=data["depth"],
            prompt=data["prompt"],
            response=data.get("response"),
            tool_calls=[
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in data.get("tool_calls", [])
            ],
            tool_results=[
                ToolResult(
                    tool_call_id=tr["tool_call_id"],
                    content=tr["content"],
                    is_error=tr.get("is_error", False),
                )
                for tr in data.get("tool_results", [])
            ],
            repl_results=[
                REPLResult(
                    output=rr["output"],
                    error=rr.get("error"),
                    execution_time_ms=rr.get("execution_time_ms", 0),
                    truncated=rr.get("truncated", False),
                )
                for rr in data.get("repl_results", [])
            ],
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            duration_ms=data.get("duration_ms", 0),
            error=data.get("error"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else datetime.now(UTC),
        )

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent trajectories.

        Args:
            limit: Maximum number of trajectories to return

        Returns:
            List of trajectory summaries (newest first)
        """
        log_files = sorted(
            self.log_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]

        summaries: list[dict[str, Any]] = []

        for log_path in log_files:
            try:
                with open(log_path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "trajectory_metadata":
                            summaries.append(
                                {
                                    "id": data["trajectory_id"],
                                    "timestamp": data["timestamp"],
                                    "calls": data["event_count"],
                                    "tokens": data["total_tokens"],
                                    "duration_ms": data["total_duration_ms"],
                                    "path": str(log_path),
                                }
                            )
            except (json.JSONDecodeError, KeyError):
                # Skip malformed files
                continue

        return summaries

    def delete_trajectory(self, trajectory_id: str) -> bool:
        """Delete a trajectory log file.

        Args:
            trajectory_id: ID of the trajectory to delete

        Returns:
            True if deleted, False if not found
        """
        log_path = self._get_log_path(trajectory_id)

        if log_path.exists():
            log_path.unlink()
            return True
        return False

    def cleanup_old(self, max_age_days: int = 7) -> int:
        """Delete trajectory logs older than max_age_days.

        Args:
            max_age_days: Maximum age in days

        Returns:
            Number of files deleted
        """
        import time

        cutoff = time.time() - (max_age_days * 24 * 60 * 60)
        deleted = 0

        for log_path in self.log_dir.glob("*.jsonl"):
            if log_path.stat().st_mtime < cutoff:
                log_path.unlink()
                deleted += 1

        return deleted
