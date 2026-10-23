"""Standalone deterministic CLI; these commands do not use an LLM."""

from __future__ import annotations

import json
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


@app.command("diagnose")
def diagnose_command(request: Path = typer.Option(..., exists=True, dir_okay=False)) -> None:
    result = diagnose(load_request(request))
    output(result)
    if not result["ready"]:
        raise typer.Exit(2)


@app.command("run")
def run_command(
    request: Path = typer.Option(..., exists=True, dir_okay=False),
    store: Path = typer.Option(Path(".snipara/validation")),
) -> None:
    result = run(load_request(request), store)
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
) -> None:
    result = verify(load_request(request), store, run_id)
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
    request: Path = typer.Option(..., exists=True, dir_okay=False), scope: str = typer.Option(...)
) -> None:
    from rlm.validation.environment import gate

    result = gate(load_request(request), scope)
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
