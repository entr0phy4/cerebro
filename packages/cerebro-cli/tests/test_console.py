"""Contract for cerebro_cli.console: capsys sees the text, pipes stay plain."""

from __future__ import annotations

import pytest

from cerebro_cli.console import error, fail, metrics, notice, plain, reveal, say, table, warn


def test_say_is_captured_on_stdout_without_color(capsys):
    say("sesion iniciada", style="ok")
    captured = capsys.readouterr()
    assert captured.out == "sesion iniciada\n"
    assert captured.err == ""
    assert "\x1b" not in captured.out


def test_say_does_not_interpret_brackets_as_markup(capsys):
    say("token a[b]c")
    assert capsys.readouterr().out == "token a[b]c\n"


def test_say_does_not_crop_a_long_line(capsys):
    line = "x" * 240
    say(line)
    assert capsys.readouterr().out == line + "\n"


def test_warn_and_error_go_to_stderr(capsys):
    warn("ya existe")
    error("token invalido")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ya existe\ntoken invalido\n"
    assert "\x1b" not in captured.err


def test_fail_exits_after_writing_stderr(capsys):
    with pytest.raises(SystemExit) as exc:
        fail("no se pudo conectar")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "no se pudo conectar\n"


def test_plain_keeps_payload_intact(capsys):
    plain("linea 1\nlinea 2 [no es markup]")
    # print() adds one trailing newline on top of whatever the payload has.
    assert capsys.readouterr().out == "linea 1\nlinea 2 [no es markup]\n"


def test_reveal_prints_the_secret_unstyled(capsys):
    reveal("creado", "sekret[a]", "guardalo ahora")
    captured = capsys.readouterr()
    assert captured.out == "creado\n\n  sekret[a]\n\nguardalo ahora\n"
    assert captured.err == ""
    assert "\x1b" not in captured.out


def test_metrics_and_notice_keep_text_and_skip_color_when_piped(capsys):
    metrics("Estadisticas", [("categorias", 2), ("documentos", 5)], styles=["count", "ok"])
    notice("Memorias por contexto", "(sin memorias todavia)")
    out = capsys.readouterr().out
    assert "Estadisticas" in out
    assert "categorias" in out and "2" in out
    assert "documentos" in out and "5" in out
    assert "Memorias por contexto" in out
    assert "(sin memorias todavia)" in out
    assert "\x1b" not in out


def test_table_status_column_keeps_the_word(capsys):
    table(["estado"], [["activo"], ["revocado"]], styles=["status"])
    out = capsys.readouterr().out
    assert "activo" in out and "revocado" in out
    assert "\x1b" not in out


def test_table_prints_headers_and_cells_without_markup(capsys):
    table(
        ["name", "email"],
        [["jose", "jose@example.com"], ["a[b]", None]],
    )
    out = capsys.readouterr().out
    assert "name" in out
    assert "email" in out
    assert "jose@example.com" in out
    assert "a[b]" in out
    assert "\x1b" not in out
