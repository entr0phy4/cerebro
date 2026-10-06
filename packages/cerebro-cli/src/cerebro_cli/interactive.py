"""TTY prompts for cerebro-cli.

Scripts stay non-interactive: if stdin or stdout is not a terminal, a missing
subcommand or required argument still exits 2, and a destructive confirm exits
asking for ``--yes``. On a TTY, ``complete_args`` walks the argparse tree,
opens a menu for a missing subcommand, and fills each empty required value
with a picker, a choice list, or a text/path/password prompt.

Pickers call the HTTP API. A failed or empty list falls back to typing the
value. Cancelling a prompt (Esc, Ctrl-C) prints ``Cancelado.`` and exits 0.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any

import questionary

from cerebro_clients import (
    AuthClient,
    CerebroAPIError,
    CerebroConnectionError,
    DocsClient,
    FlowsClient,
    MemoryClient,
)
from cerebro_cli.console import fail, say, warn

_REQUIRED_NOTE = "obligatorio en scripts"
_MANUAL = "__cerebro_manual__"
_LIST_LIMIT = 100

Picker = Callable[[argparse.Namespace], str]


def is_interactive() -> bool:
    """True only when both stdin and stdout are terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def cancelled() -> None:
    """User dismissed a prompt. Exit 0, same as declining a confirm."""
    say("Cancelado.", style="muted")
    raise SystemExit(0)


def _answer(question: Any) -> str:
    while True:
        result = question.ask()
        if result is None:
            cancelled()
        text = str(result).strip()
        if text == "":
            warn("El valor no puede estar vacio.")
            continue
        return text


def confirm(message: str) -> bool:
    """Yes/no, default no. Without a TTY, exit asking for ``--yes``."""
    if not is_interactive():
        fail("No hay terminal interactiva. Pasa --yes para confirmar.")
    result = questionary.confirm(message, default=False).ask()
    if result is None:
        cancelled()
    return bool(result)


def prompt_text(message: str) -> str:
    return _answer(questionary.text(message))


def prompt_secret(message: str) -> str:
    return _answer(questionary.password(message))


def prompt_path(message: str) -> str:
    return _answer(questionary.path(message))


def select(message: str, options: Sequence[tuple[str, str]]) -> str:
    """Arrow-key choice. ``options`` is ``(label, value)``."""
    choices = [questionary.Choice(title=label, value=value) for label, value in options]
    result = questionary.select(message, choices=choices).ask()
    if result is None:
        cancelled()
    return str(result)


def require(
    parser: argparse.ArgumentParser,
    *name_or_flags: str,
    picker: str | None = None,
    secret: bool = False,
    path: bool = False,
    **kwargs: Any,
) -> None:
    """``add_argument`` that ``complete_args`` will prompt for when it is missing.

    Positionals become ``nargs='?'`` so a partial command still parses. Required
    flags drop ``required=True`` for the same reason. Help text keeps the note
    that scripts must pass the value.
    """
    positional = not str(name_or_flags[0]).startswith("-")
    if positional:
        kwargs["nargs"] = "?"
        kwargs["default"] = None
    else:
        kwargs["required"] = False
        kwargs.setdefault("default", None)
    help_text = kwargs.get("help")
    if help_text:
        kwargs["help"] = f"{help_text} ({_REQUIRED_NOTE})"
    else:
        kwargs["help"] = _REQUIRED_NOTE
    action = parser.add_argument(*name_or_flags, **kwargs)
    required: list[str] = getattr(parser, "interactive_required", [])
    required.append(action.dest)
    parser.interactive_required = required
    if picker:
        pickers: dict[str, str] = getattr(parser, "interactive_pickers", {})
        pickers[action.dest] = picker
        parser.interactive_pickers = pickers
    if secret:
        secrets: set[str] = getattr(parser, "interactive_secrets", set())
        secrets.add(action.dest)
        parser.interactive_secrets = secrets
    if path:
        paths: set[str] = getattr(parser, "interactive_paths", set())
        paths.add(action.dest)
        parser.interactive_paths = paths


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse.Action | None:
    group = parser._subparsers
    if group is None:
        return None
    actions = getattr(group, "_group_actions", ())
    return actions[0] if actions else None


def _locate(root: argparse.ArgumentParser, args: argparse.Namespace) -> argparse.ArgumentParser:
    current = root
    while True:
        action = _subparsers_action(current)
        if action is None:
            return current
        chosen = getattr(args, action.dest, None)
        choices = action.choices
        if not chosen or choices is None or chosen not in choices:
            return current
        current = choices[chosen]


def _action(parser: argparse.ArgumentParser, dest: str) -> argparse.Action:
    for action in parser._actions:
        if action.dest == dest:
            return action
    raise KeyError(dest)


def _label(parser: argparse.ArgumentParser, dest: str) -> str:
    action = _action(parser, dest)
    if action.option_strings:
        return action.option_strings[0]
    return dest


def _question(action: argparse.Action) -> str:
    name = action.option_strings[0] if action.option_strings else action.dest
    help_text = "" if action.help in (None, argparse.SUPPRESS) else str(action.help)
    suffix = f" ({_REQUIRED_NOTE})"
    if help_text.endswith(suffix):
        help_text = help_text[: -len(suffix)]
    help_text = help_text.strip()
    if help_text and help_text != _REQUIRED_NOTE:
        return f"{name} ({help_text})"
    return name


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _load(fetch: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    try:
        return fetch()
    except CerebroConnectionError as exc:
        warn(f"No se pudo listar: {exc}")
        return None
    except CerebroAPIError as exc:
        warn(f"No se pudo listar ({exc.status_code}): {exc.detail}")
        return None


def _choose_or_type(message: str, rows: list[tuple[str, str]] | None) -> str:
    if rows is None:
        return prompt_text(message)
    if not rows:
        warn("No hay elementos para elegir.")
        return prompt_text(message)
    chosen = select(message, [*rows, ("escribir el valor", _MANUAL)])
    if chosen == _MANUAL:
        return prompt_text(message)
    return chosen


def _pick_docs_category(_args: argparse.Namespace) -> str:
    categories = _load(lambda: DocsClient().list_categories())
    if categories is None:
        return prompt_text("categoria")
    rows = [(f"{c['slug']} — {_one_line(c.get('name') or c['slug'])}", c["slug"]) for c in categories]
    return _choose_or_type("categoria", rows)


def _pick_docs_document(_args: argparse.Namespace) -> str:
    documents = _load(lambda: DocsClient().list_documents(limit=_LIST_LIMIT))
    if documents is None:
        return prompt_text("document_id")
    rows = [
        (f"{d['category']}/{d['slug']} — {_one_line(d['title'])}", str(d["id"]))
        for d in documents
    ]
    return _choose_or_type("documento", rows)


def _pick_docs_slug(args: argparse.Namespace) -> str:
    category = getattr(args, "category", None)
    if not category:
        return prompt_text("slug")
    documents = _load(lambda: DocsClient().list_documents(category=category, limit=_LIST_LIMIT))
    if documents is None:
        return prompt_text("slug")
    rows = [
        (f"{d['category']}/{d['slug']} — {_one_line(d['title'])}", d["slug"])
        for d in documents
    ]
    return _choose_or_type("slug", rows)


def _pick_archived_document(_args: argparse.Namespace) -> str:
    documents = _load(lambda: DocsClient().list_archived_documents(limit=_LIST_LIMIT))
    if documents is None:
        return prompt_text("document_id")
    rows = [
        (f"{d['category']}/{d['slug']} — {_one_line(d['title'])}", str(d["id"]))
        for d in documents
    ]
    return _choose_or_type("documento", rows)


def _pick_flow_code(_args: argparse.Namespace) -> str:
    flows = _load(lambda: FlowsClient().list_flows(limit=_LIST_LIMIT))
    if flows is None:
        return prompt_text("code")
    rows = [(f"{f['code']} — {_one_line(f['name'])}", f["code"]) for f in flows]
    return _choose_or_type("flujo", rows)


def _pick_memory_context(_args: argparse.Namespace) -> str:
    contexts = _load(lambda: MemoryClient().list_contexts())
    if contexts is None:
        return prompt_text("context")
    rows = [(f"{c['slug']} — {_one_line(c.get('name') or c['slug'])}", c["slug"]) for c in contexts]
    return _choose_or_type("contexto", rows)


def _pick_auth_token(_args: argparse.Namespace) -> str:
    tokens = _load(lambda: AuthClient().list_tokens())
    if tokens is None:
        return prompt_text("name")
    rows = [(_one_line(t["name"]), t["name"]) for t in tokens]
    return _choose_or_type("token", rows)


def _pick_auth_user(_args: argparse.Namespace) -> str:
    users = _load(lambda: AuthClient().list_users())
    if users is None:
        return prompt_text("user")
    rows = [(_one_line(u["name"]), u["name"]) for u in users]
    return _choose_or_type("usuario", rows)


_PICKERS: dict[str, Picker] = {
    "docs.category": _pick_docs_category,
    "docs.document": _pick_docs_document,
    "docs.document_slug": _pick_docs_slug,
    "docs.archived_document": _pick_archived_document,
    "flow.code": _pick_flow_code,
    "memory.context": _pick_memory_context,
    "auth.token": _pick_auth_token,
    "auth.user": _pick_auth_user,
}


def _prompt_for(parser: argparse.ArgumentParser, dest: str, args: argparse.Namespace) -> str:
    picker_key = getattr(parser, "interactive_pickers", {}).get(dest)
    if picker_key:
        return _PICKERS[picker_key](args)
    action = _action(parser, dest)
    message = _question(action)
    if dest in getattr(parser, "interactive_secrets", set()):
        return prompt_secret(message)
    if dest in getattr(parser, "interactive_paths", set()):
        return prompt_path(message)
    if action.choices:
        return select(message, [(str(choice), str(choice)) for choice in action.choices])
    return prompt_text(message)


def _select_subcommand(action: argparse.Action) -> str:
    options: list[tuple[str, str]] = []
    for choice in getattr(action, "_choices_actions", ()):
        label = choice.dest
        if choice.help:
            label = f"{choice.dest} — {choice.help}"
        options.append((label, choice.dest))
    title = action.dest.replace("_", " ")
    return select(title, options)


def complete_args(root: argparse.ArgumentParser, argv: list[str] | None = None) -> argparse.Namespace:
    """Parse ``argv``, prompting on a TTY for whatever is still missing.

    ``argv`` defaults to ``sys.argv[1:]``. A complete command never prompts,
    even on a TTY.
    """
    if argv is None:
        argv = sys.argv[1:]
    pending = list(argv)
    while True:
        args = root.parse_args(pending)
        current = _locate(root, args)
        action = _subparsers_action(current)
        if action is not None and getattr(args, action.dest, None) is None:
            if not is_interactive():
                current.error(f"the following arguments are required: {action.dest}")
            pending.append(_select_subcommand(action))
            continue
        missing = [
            dest
            for dest in getattr(current, "interactive_required", [])
            if getattr(args, dest, None) is None
        ]
        if not missing:
            return args
        if not is_interactive():
            labels = ", ".join(_label(current, dest) for dest in missing)
            current.error(f"the following arguments are required: {labels}")
        for dest in missing:
            setattr(args, dest, _prompt_for(current, dest, args))
        return args
