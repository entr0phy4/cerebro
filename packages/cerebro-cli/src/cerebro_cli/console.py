"""Terminal output for cerebro-cli.

Commands print through this module instead of calling ``print()`` directly.
Two Rich consoles (``out``, ``err``) cover stdout and stderr. ``file`` is left
unset on purpose: Rich resolves ``sys.stdout`` / ``sys.stderr`` on each write,
so pytest's ``capsys`` captures the text the same way it captures ``print()``.
A ``Console(file=sys.stdout)`` built at import time would freeze the stream
and bypass that capture.

Color, markup, and highlighting are off unless a helper asks for a style.
User data (names, slugs, document bodies, tokens) must not be interpreted as
Rich markup. ``plain()`` is for anything another program might read from
stdout — document bodies, YAML, dumps — and is written without wrapping or
styling. ``reveal()`` is the one-time-secret case: a styled confirmation, the
value itself via ``plain()``, then a warning.

``out`` and ``err`` stay available for Rich features the helpers don't wrap
yet (``Progress``, ``Status``). Pass them as ``console=``; don't construct a
second ``Console``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from rich import box
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

_THEME = Theme(
    {
        "ok": "green",
        "warning": "yellow",
        "error": "red",
        "muted": "dim",
        "heading": "bold cyan",
        "label": "cyan",
        "count": "bold",
    }
)

# Whole-cell values colored when a column style is ``status``. Anything else
# stays literal text — a title that happens to contain the word is not restyled.
_STATUS_STYLES = {
    "active": "ok",
    "activo": "ok",
    "completed": "ok",
    "admin": "warning",
    "owner": "label",
    "in_progress": "warning",
    "archived": "warning",
    "archivado": "warning",
    "revocado": "error",
    "revoked": "error",
    "aborted": "error",
    "superseded": "muted",
    "user": "muted",
    "draft": "muted",
}

# markup/emoji/highlight default off: a name like "a[b]" must survive intact.
out = Console(theme=_THEME, markup=False, emoji=False, highlight=False)
err = Console(stderr=True, theme=_THEME, markup=False, emoji=False, highlight=False)


def say(message: str = "", *, style: str | None = None) -> None:
    """One line on stdout. ``style`` is a theme name (``ok``, ``muted``) or a Rich style."""
    out.print(message, style=style, markup=False, highlight=False, soft_wrap=True, crop=False)


def warn(message: str) -> None:
    """One line on stderr, yellow when stderr is a TTY."""
    err.print(message, style="warning", markup=False, highlight=False, soft_wrap=True, crop=False)


def error(message: str) -> None:
    """One line on stderr, red when stderr is a TTY."""
    err.print(message, style="error", markup=False, highlight=False, soft_wrap=True, crop=False)


def fail(message: str, code: int = 1) -> None:
    """``error(message)`` and exit. Replaces ``print(..., file=sys.stderr); sys.exit(code)``."""
    error(message)
    sys.exit(code)


def plain(text: str) -> None:
    """Write ``text`` exactly, plus a trailing newline. No markup, color, or reflow.

    Use this for document bodies, YAML, tokens, and any other payload a script
    might parse. A value that already ends in a newline still gets one more,
    matching ``print()``.
    """
    out.print(text, markup=False, highlight=False, soft_wrap=True, crop=False, end="\n")


def reveal(intro: str, value: str, hint: str) -> None:
    """Confirmation, the secret on its own line, then a warning.

    ``value`` goes through ``plain`` so a pipe or a copy gets the raw text,
    indented the way ``print(f"  {token}")`` used to.
    """
    say(intro, style="ok")
    say()
    plain(f"  {value}")
    say()
    say(hint, style="warning")


def _frame(title: str, body: RenderableType) -> None:
    """Panel around a stats block. The title is a ``Text``, so it is not markup."""
    out.print(
        Panel(
            body,
            title=Text(title, style="heading"),
            title_align="left",
            border_style="heading",
            box=box.ROUNDED if out.is_terminal else box.ASCII,
            padding=(0, 1),
            expand=False,
        )
    )


def notice(title: str, message: str) -> None:
    """Titled panel with one muted line. For an empty stats section."""
    _frame(title, Text(message, style="muted"))


def metrics(
    title: str,
    pairs: Sequence[tuple[str, object]],
    *,
    styles: Sequence[str | None] | None = None,
) -> None:
    """Label/value stats inside a panel. Values are literal text, not markup.

    ``styles`` colors each value (theme name). Labels use ``label``. Omitted
    styles fall back to ``count``.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column(justify="right")
    for index, (label, value) in enumerate(pairs):
        value_style = "count"
        if styles is not None and index < len(styles) and styles[index]:
            value_style = styles[index]
        grid.add_row(Text(str(label), style="label"), Text("" if value is None else str(value), style=value_style))
    _frame(title, grid)


def _cell(value: object, column_style: str | None) -> Text:
    raw = "" if value is None else str(value)
    if column_style == "status":
        return Text(raw, style=_STATUS_STYLES.get(raw))
    return Text(raw, style=column_style)


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    title: str | None = None,
    styles: Sequence[str | None] | None = None,
    frame: str | None = None,
) -> None:
    """Aligned columns on stdout. Cell values are not parsed as markup.

    ``styles`` is one theme name per column (``label``, ``count``, ``muted``,
    ``status``). ``status`` colors a cell only when its whole value is a known
    state word. ``frame`` wraps the table in a panel — used by stats.

    A TTY gets a header rule; a pipe gets ASCII, so logs stay readable.
    Cells fold instead of being ellipsized — ids and slugs must not be cut.
    """
    grid = Table(
        title=Text(title, style="heading") if title else None,
        header_style="heading",
        box=box.SIMPLE_HEAD if out.is_terminal else box.ASCII,
        show_edge=False,
        pad_edge=False,
        safe_box=True,
    )
    for index, header in enumerate(headers):
        column_style = styles[index] if styles is not None and index < len(styles) else None
        grid.add_column(header, overflow="fold", style=None if column_style == "status" else column_style)
    for row in rows:
        grid.add_row(
            *(
                _cell(cell, styles[index] if styles is not None and index < len(styles) else None)
                for index, cell in enumerate(row)
            )
        )
    if frame:
        _frame(frame, grid)
    else:
        out.print(grid)
