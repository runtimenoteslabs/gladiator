"""Display results in a formatted terminal table."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text

from llm_judge.judge import ModelResult


def print_results(prompt: str, results: list[ModelResult]):
    console = Console()

    console.print()
    console.print(Panel(prompt, title="Prompt", border_style="cyan"))
    console.print()

    # Summary table
    table = Table(title="Comparison Summary", show_lines=True)
    table.add_column("Model", style="bold")
    table.add_column("Latency", justify="right")
    table.add_column("Tokens (in/out)", justify="right")
    table.add_column("Status")

    for r in results:
        if r.error:
            status = Text(f"Error: {r.error}", style="red")
        else:
            status = Text("OK", style="green")

        table.add_row(
            r.model,
            f"{r.latency_ms:.0f}ms",
            f"{r.input_tokens}/{r.output_tokens}",
            status,
        )

    console.print(table)
    console.print()

    # Side-by-side responses
    panels = []
    for r in results:
        if r.error:
            content = Text(f"Error: {r.error}", style="red")
        else:
            content = r.response

        panels.append(
            Panel(
                content,
                title=f"{r.model} ({r.latency_ms:.0f}ms)",
                border_style="green" if not r.error else "red",
                width=60,
            )
        )

    console.print(Columns(panels, equal=True))
    console.print()
