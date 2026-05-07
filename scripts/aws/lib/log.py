import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from rich.console import Console

_console = Console(force_terminal=True, legacy_windows=False)


class Log:
    def info(self, msg: str) -> None:
        _console.print(f"[cyan]\\[i][/]  {msg}")

    def success(self, msg: str) -> None:
        _console.print(f"[green]\\[+][/] {msg}")

    def warn(self, msg: str) -> None:
        _console.print(f"[yellow]\\[!][/]  {msg}")

    def err(self, msg: str) -> None:
        _console.print(f"[red]\\[x][/] {msg}")

    def step(self, msg: str) -> None:
        _console.print()
        _console.print(f"[magenta bold]>> {msg}[/]")


log = Log()
