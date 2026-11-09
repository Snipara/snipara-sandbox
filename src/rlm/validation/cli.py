"""Standalone deterministic CLI; these commands do not use an LLM."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import typer

from rlm.validation.models import ValidationRequest
from rlm.validation.runner import cancel, diagnose, read_run, run, verify

app = typer.Typer(
    help="Validate explicit repository snapshots in Docker; no automatic installation or host fallback."
)


def load_request(path: Path) -> ValidationRequest:
    if path.stat().st_size > 65536:
        raise ValueError("request exceeds 64 KiB")
    return ValidationRequest.model_validate_json(path.read_text())


def output(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))


def directory_fd(value: int | None) -> int | None:
    if value is not None and not stat.S_ISDIR(os.fstat(value).st_mode):
        raise typer.BadParameter("workspace-fd must identify an inherited directory")
    return value


@app.command("profiles")
def profiles_command() -> None:
    """List prepared dependency contracts without contacting Docker or installing tools."""
    from rlm.validation.profiles_catalog import validation_profiles

    output(validation_profiles())


@app.command("inspect")
def inspect_command(request: Path = typer.Option(..., exists=True, dir_okay=False)) -> None:
    """Inspect the authoritative request identity without executing a readiness probe."""
    from rlm.validation.profiles_catalog import inspect_request

    output(inspect_request(load_request(request)))


@app.command("prepare")
def prepare_command(
    profile: str = typer.Option(...),
    workspace: str = typer.Option(...),
    image: str = typer.Option(...),
    command: list[str] = typer.Argument(None),
    output_file: Path | None = typer.Option(None, "--output", dir_okay=False),
) -> None:
    """Print a reviewable request; never build an image, install dependencies or run it."""
    from rlm.validation.profiles_catalog import prepare_request

    request = prepare_request(profile, workspace, image, command or None)
    value = request.model_dump(mode="json")
    if output_file is not None:
        with output_file.open("x", encoding="utf-8") as destination:
            destination.write(json.dumps(value, indent=2) + "\n")
    output(value)


@app.command("diagnose")
def diagnose_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    probe_timeout_seconds: int = typer.Option(30, min=1, max=60),
    workspace_fd: int | None = typer.Option(None, min=0, hidden=True),
) -> None:
    result = diagnose(
        load_request(request),
        probe_timeout_seconds=probe_timeout_seconds,
        workspace_fd=directory_fd(workspace_fd),
    )
    output(result)
    if not result["ready"]:
        raise typer.Exit(2)


@app.command("run")
def run_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    store: Path = typer.Option(Path(".snipara/validation")),
    workspace_fd: int | None = typer.Option(None, min=0, hidden=True),
) -> None:
    import signal
    import threading

    event = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda *_: event.set())
    try:
        result = run(
            load_request(request),
            store,
            cancel_event=event,
            workspace_fd=directory_fd(workspace_fd),
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    output(result)
    if result["status"] != "passed":
        raise typer.Exit(1)


@app.command("status")
def status_command(
    run_id: str = typer.Option(...), store: Path = typer.Option(Path(".snipara/validation"))
) -> None:
    output(read_run(store, run_id))


@app.command("verify")
def verify_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    run_id: str = typer.Option(...),
    store: Path = typer.Option(Path(".snipara/validation")),
    workspace_fd: int | None = typer.Option(None, min=0, hidden=True),
) -> None:
    result = verify(load_request(request), store, run_id, workspace_fd=directory_fd(workspace_fd))
    output(result)
    if not result["valid"]:
        raise typer.Exit(1)


@app.command("cancel")
def cancel_command(
    run_id: str = typer.Option(...), store: Path = typer.Option(Path(".snipara/validation"))
) -> None:
    output(cancel(store, run_id))


@app.command("gate")
def gate_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    scope: str = typer.Option(...),
    probe_timeout_seconds: int = typer.Option(30, min=1, max=60),
    workspace_fd: int | None = typer.Option(None, min=0, hidden=True),
) -> None:
    from rlm.validation.environment import gate

    result = gate(
        load_request(request),
        scope,
        probe_timeout_seconds=probe_timeout_seconds,
        workspace_fd=directory_fd(workspace_fd),
    )
    output(result)
    if result["status"] == "needs_user_choice":
        raise typer.Exit(2)


@app.command("choose")
def choose_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    scope: str = typer.Option(...),
    choice: str = typer.Option(...),
    environment: str = typer.Option(""),
    acknowledge_user_choice: bool = typer.Option(False),
) -> None:
    from rlm.validation.environment import record_choice

    output(
        record_choice(
            load_request(request),
            scope,
            choice,
            acknowledged=acknowledge_user_choice,
            environment=environment,
        )
    )


@app.command("recover")
def recover_command(
    run_id: str = typer.Option(...), store: Path = typer.Option(Path(".snipara/validation"))
) -> None:
    from rlm.validation.runner import recover

    output(recover(store, run_id))
