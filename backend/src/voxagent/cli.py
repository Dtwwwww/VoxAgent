import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import typer

from voxagent.config import AppPaths, resolve_data_root
from voxagent.diagnostics.hardware import collect_hardware, evaluate_preflight
from voxagent.diagnostics.llm_benchmark import run_llm_benchmark
from voxagent.llm.ollama import OllamaClient

app = typer.Typer(no_args_is_help=True)

LLM_BENCHMARK_PROMPTS = (
    "请用两句自然中文介绍你自己，每句不超过二十个字。",
    "用户说他喜欢喝无糖咖啡。请只输出一条适合长期保存的记忆。",
    "用户要求打开记事本。请说明需要调用工具，不要声称已经完成。",
)

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


@app.command("benchmark-llm")
def benchmark_llm(
    model: Annotated[str, typer.Option("--model")] = "",
    output: Annotated[Path, typer.Option("--output", dir_okay=False)] = Path("benchmark.json"),
) -> None:
    if not model.strip():
        raise typer.BadParameter("--model is required")

    async def execute() -> dict[str, object]:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:11434") as http:
            result = await run_llm_benchmark(OllamaClient(http), model, LLM_BENCHMARK_PROMPTS)
            return result.to_dict()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(asyncio.run(execute()), ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    app()
