"""Interactive terminal chat for the mutual fund overlap agent — the only
interface. A thin REPL around `run_agent`; conversation history lives in
this process for the session's lifetime, passed straight through on each call.

Run with: python -m app.cli
"""

import asyncio

from rich.console import Console
from rich.markdown import Markdown

import app.models  # noqa: F401 — registers models on Base before create_all
from app.agent.orchestrator import run_agent
from app.db import Base, async_session, engine

EXIT_COMMANDS = {"exit", "quit", ":q", "/exit"}


async def _ensure_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def main() -> None:
    console = Console()
    await _ensure_tables()

    console.print(
        "[bold cyan]Mutual Fund Overlap Assistant[/bold cyan] — "
        "ask about fund overlap or portfolio concentration.\n"
        "[dim]Type 'exit' to quit.[/dim]\n"
    )

    history: list[dict] = []

    async with async_session() as session:
        while True:
            try:
                message = await asyncio.to_thread(console.input, "[bold green]›[/bold green] ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            message = message.strip()
            if not message:
                continue
            if message.lower() in EXIT_COMMANDS:
                break

            with console.status("[dim]thinking...[/dim]", spinner="dots"):
                try:
                    reply, history = await run_agent(message, session, history=history)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

            console.print(Markdown(reply))
            console.print()


if __name__ == "__main__":
    asyncio.run(main())
