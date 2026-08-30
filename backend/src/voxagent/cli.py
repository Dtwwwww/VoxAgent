import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from voxagent.config import AppPaths, resolve_data_root
from voxagent.diagnostics.hardware import collect_hardware, evaluate_preflight

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run VoxAgent local diagnostics."""


@app.command("paths")
def show_paths(
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    paths.create()
    typer.echo(str(paths.root))


@app.command("preflight")
def preflight(
    data_root: Annotated[Path | None, typer.Option("--data-root", dir_okay=True)] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    snapshot = collect_hardware(paths)
    issues = evaluate_preflight(snapshot)
    payload = {"hardware": snapshot.to_dict(), "issues": [asdict(issue) for issue in issues]}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo("PASS" if not issues else "BLOCKED")
    if any(issue.blocking for issue in issues):
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
