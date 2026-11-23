"""Standalone deterministic CLI; these commands do not use an LLM."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import typer

from rlm.validation.models import ValidationRequest
from rlm.validation.runner import cancel, diagnose, read_run, run, verify, with_output_tail

app = typer.Typer(
    help="Validate explicit repository snapshots in Docker; no automatic installation or host fallback."
)


def parse_request(path: Path) -> ValidationRequest | str:
    """Return the request, or a one-line reason why the file is unusable."""
    from pydantic import ValidationError

    try:
        if path.stat().st_size > 65536:
            raise ValueError("request exceeds 64 KiB")
        return ValidationRequest.model_validate_json(path.read_text())
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in item['loc']) or 'request'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False)[:8]
        ]
        remaining = error.error_count() - len(problems)
        if remaining > 0:
            problems.append(f"and {remaining} more")
        reason = "; ".join(problems)
    except (OSError, ValueError) as error:
        reason = str(error)
    return f"Invalid validation request {path.name}: {reason}"[:2000]


def load_request(path: Path) -> ValidationRequest:
    """Load a request, reporting an unusable file as one line instead of a traceback."""
    request = parse_request(path)
    if isinstance(request, str):
        typer.echo(request, err=True)
        raise typer.Exit(2)
    return request


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


@app.command("engine")
def engine_command(
    image: str = typer.Option(
        ...,
        help="A local image (any small Linux image with a shell) to create the probe container from.",
    ),
) -> None:
    """Check that the selected container engine enforces the isolation a validation job relies on."""
    from rlm.validation.engine_check import engine_check

    result = engine_check(image)
    output(result)
    if not result["ok"]:
        raise typer.Exit(2)


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
    scope: str | None = typer.Option(
        None,
        help="Workflow/lot scope: hold one lock per identical work and reuse a freshly verified "
        "passing receipt instead of executing again.",
    ),
    no_reuse: bool = typer.Option(
        False, "--no-reuse", help="Execute fresh while retaining scoped exclusion (needs --scope)."
    ),
    coordination_root: Path | None = typer.Option(
        None,
        file_okay=False,
        help="Confine the scoped request, workspace, store and reuse state to this operator root.",
    ),
    workspace_fd: int | None = typer.Option(None, min=0, hidden=True),
) -> None:
    """Run a request once in Docker; with --scope, coordinate and reuse verified work."""
    import signal
    import threading

    if scope is None and (no_reuse or coordination_root is not None):
        from rlm.validation.coordinator import blocked

        output(blocked("--no-reuse and --coordination-root require an explicit --scope."))
        raise typer.Exit(2)
    event = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda *_: event.set())
    try:
        if scope is None:
            result = run(
                load_request(request),
                store,
                cancel_event=event,
                workspace_fd=directory_fd(workspace_fd),
            )
        else:
            from rlm.validation.coordinator import blocked, coordinated_run
            from rlm.validation.paths import canonical_system_path

            loaded = parse_request(request)
            if isinstance(loaded, str):
                result = blocked(loaded, reason_code="request_invalid")
            elif coordination_root is not None and not canonical_system_path(
                request
            ).is_relative_to(canonical_system_path(coordination_root)):
                result = blocked("Request must remain within the coordination root.")
            else:
                result = coordinated_run(
                    loaded,
                    store,
                    scope=scope,
                    root=coordination_root,
                    no_reuse=no_reuse,
                    cancel_event=event,
                    workspace_fd=directory_fd(workspace_fd),
                )
    finally:
        signal.signal(signal.SIGTERM, previous)
    result = with_output_tail(result, store)
    output(result)
    if scope is not None:
        state = result.get("work_reuse", {}).get("state")
        if result.get("status") != "passed" or state not in {"executed", "reused"}:
            raise typer.Exit(2)
    elif result["status"] != "passed":
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
