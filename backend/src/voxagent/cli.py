from pathlib import Path
from typing import Annotated

import typer

from voxagent.config import AppPaths, resolve_data_root

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


if __name__ == "__main__":
    app()
