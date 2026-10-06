"""Interactive completion: menus, required-arg prompts, and id pickers.

questionary is patched, so nothing here touches a real terminal or the network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import cerebro_cli.interactive as interactive
from cerebro_cli import memory_commands
from cerebro_cli.interactive import complete_args, confirm
from cerebro_cli.main import build_parser, main
from cerebro_clients import CerebroConnectionError


def _queued(values):
    pending = iter(values)

    def factory(*_args, **_kwargs):
        question = MagicMock()
        question.ask.return_value = next(pending)
        return question

    return factory


def test_menu_reaches_stats(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(interactive.questionary, "select", _queued(["memory", "stats"]))
    args = complete_args(build_parser(), [])
    assert args.func is memory_commands.cmd_stats
    assert args.memory_command == "stats"


def test_picker_fills_document_id(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    docs = [{"id": "doc-1", "category": "eco", "slug": "mi-doc", "title": "Titulo"}]
    monkeypatch.setattr(interactive.DocsClient, "list_documents", lambda self, **_kwargs: docs)
    monkeypatch.setattr(interactive.questionary, "select", _queued(["doc-1"]))
    args = complete_args(build_parser(), ["docs", "delete"])
    assert args.document_id == "doc-1"
    assert args.func.__name__ == "cmd_delete"


def test_get_picks_category_then_document_slug(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(
        interactive.DocsClient,
        "list_categories",
        lambda self: [{"slug": "eco", "name": "Eco"}],
    )
    seen = {}

    def list_documents(self, **kwargs):
        seen["category"] = kwargs.get("category")
        return [{"id": "1", "category": "eco", "slug": "mi-doc", "title": "Titulo"}]

    monkeypatch.setattr(interactive.DocsClient, "list_documents", list_documents)
    monkeypatch.setattr(interactive.questionary, "select", _queued(["eco", "mi-doc"]))
    args = complete_args(build_parser(), ["docs", "get"])
    assert (args.category, args.slug) == ("eco", "mi-doc")
    assert seen["category"] == "eco"


def test_empty_picker_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(interactive.DocsClient, "list_documents", lambda self, **_kwargs: [])
    monkeypatch.setattr(interactive.questionary, "text", _queued(["typed-id"]))
    args = complete_args(build_parser(), ["docs", "delete"])
    assert args.document_id == "typed-id"


def test_picker_api_error_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)

    def boom(self, **_kwargs):
        raise CerebroConnectionError("http://x", RuntimeError("refused"))

    monkeypatch.setattr(interactive.DocsClient, "list_documents", boom)
    monkeypatch.setattr(interactive.questionary, "text", _queued(["typed-id"]))
    args = complete_args(build_parser(), ["docs", "delete"])
    assert args.document_id == "typed-id"


def test_prompts_required_flag(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(interactive.questionary, "text", _queued(["agente-x", "read,write"]))
    args = complete_args(build_parser(), ["token", "create"])
    assert args.name == "agente-x"
    assert args.scopes == "read,write"
    assert args.func.__name__ == "cmd_token_create"


def test_login_token_is_a_password_prompt(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(interactive.questionary, "password", _queued(["cbr_secret"]))

    def text_should_not_run(*_args, **_kwargs):
        raise AssertionError("login --token must not echo")

    monkeypatch.setattr(interactive.questionary, "text", text_should_not_run)
    args = complete_args(build_parser(), ["login"])
    assert args.token == "cbr_secret"


def test_cancel_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(interactive.questionary, "select", _queued([None]))
    with pytest.raises(SystemExit) as exc:
        complete_args(build_parser(), [])
    assert exc.value.code == 0
    assert "Cancelado." in capsys.readouterr().out


def test_missing_command_without_tty_exits_2(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)
    with pytest.raises(SystemExit) as exc:
        complete_args(build_parser(), [])
    assert exc.value.code == 2


def test_missing_document_without_tty_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)
    with pytest.raises(SystemExit) as exc:
        complete_args(build_parser(), ["docs", "delete"])
    assert exc.value.code == 2
    assert "document_id" in capsys.readouterr().err


def test_complete_argv_does_not_prompt(monkeypatch):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)

    def boom(*_args, **_kwargs):
        raise AssertionError("should not prompt")

    monkeypatch.setattr(interactive.questionary, "select", boom)
    monkeypatch.setattr(interactive.questionary, "text", boom)
    args = complete_args(build_parser(), ["docs", "stats"])
    assert args.func.__name__ == "cmd_stats"


def test_confirm_without_tty_asks_for_yes(monkeypatch, capsys):
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)
    with pytest.raises(SystemExit) as exc:
        confirm("borrar?")
    assert exc.value.code == 1
    assert "--yes" in capsys.readouterr().err


def test_main_dispatches_completed_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(interactive, "is_interactive", lambda: False)
    monkeypatch.setattr(memory_commands, "cmd_stats", lambda args: seen.setdefault("ran", args.memory_command))
    main(["memory", "stats"])
    assert seen["ran"] == "stats"


def test_delete_help_keeps_required_note():
    parser = build_parser()
    docs = parser._subparsers._group_actions[0].choices["docs"]
    delete = docs._subparsers._group_actions[0].choices["delete"]
    text = delete.format_help()
    assert "document_id" in text
    assert "obligatorio en scripts" in text
