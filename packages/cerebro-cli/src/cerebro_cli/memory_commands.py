"""`cerebro memory <subcommand>` subcommands -- a 1:1 port of the original
`cerebro_memory.cli`, replacing the direct httpx calls with `MemoryClient`
(`cerebro_clients`). The only logic of its own left here (outside the client) is the
Markdown importer's orchestration (`_collect_memories`/`_ensure_context`/
`_is_duplicate`), same as in the original: the pure parsing still lives in
`cerebro_memory.markdown_importer`, it isn't duplicated.

`token create/list/revoke` here are SCOPED to cerebro-memory only (same
behavior as the original `cerebro-memory token`) -- distinct from the root-level
`cerebro token create/revoke`, which is CROSS-CUTTING (see shared_commands.py and
ecosistema-cerebro.md SS13).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cerebro_clients import CerebroAPIError, CerebroConnectionError, MemoryClient
from cerebro_memory.markdown_importer import ParsedMemory, iter_markdown_files, parse_markdown_file

from cerebro_cli.console import fail, metrics, notice, reveal, say, table, warn

# The RRF score of a hit that wins rank #1 in both vector search and full
# text search is ~2/(60+1) ~= 0.033 (see cerebro_memory.retrieval.reciprocal_rank_fusion,
# k=60 default). A near-exact duplicate of the same content should land there;
# we set the threshold a bit below that to tolerate minor variation without opening the
# door to false positives (the identical-title requirement, enforced separately, is the
# main safeguard).
DEDUP_SCORE_THRESHOLD = 0.02

DISAMBIGUATION_TRAINING_THRESHOLD = 500


def _client() -> MemoryClient:
    return MemoryClient()


def _fail_request(exc: CerebroConnectionError | CerebroAPIError) -> None:
    if isinstance(exc, CerebroConnectionError):
        fail(f"No se pudo conectar con cerebro-memory: {exc}")
    else:
        fail(f"La API devolvio {exc.status_code}: {exc.detail}")


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    rows = data.get("memories_by_context", [])
    if not rows:
        notice("Memorias por contexto", "(sin memorias todavia)")
    else:
        table(
            ["context", "status", "count"],
            [[row["context"], row["status"], row["count"]] for row in rows],
            styles=["label", "status", "count"],
            frame="Memorias por contexto",
        )

    d = data.get("disambiguations", {})
    unresolved = d.get("unresolved", 0)
    metrics(
        "Desambiguaciones (Context Engine, Fase 2/4)",
        [
            ("total", d.get("total", 0)),
            ("auto (scoring determinista)", d.get("auto", 0)),
            ("local_model (Fase 4)", d.get("local_model", 0)),
            ("agent (agente/MCP eligio)", d.get("agent", 0)),
            ("user", d.get("user", 0)),
            ("sin resolver", unresolved),
        ],
        styles=["count", "ok", "muted", "label", "label", "warning" if unresolved else "muted"],
    )

    prefs = data.get("preferences_learned", [])[:20]
    if not prefs:
        notice("Preferencias aprendidas (top 20 por peso)", "(ninguna todavia)")
    else:
        table(
            ["context", "term", "peso"],
            [[p["context"], p["term"], f"{p['weight']:.2f}"] for p in prefs],
            styles=["label", None, "count"],
            frame="Preferencias aprendidas (top 20 por peso)",
        )


# --------------------------------------------------------------------------- export-disambiguations


def cmd_export_disambiguations(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        rows = client.export_disambiguations(resolved_only=args.resolved_only)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    out_path = Path(args.output) if args.output else Path("disambiguations_export.jsonl")
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(rows)
    say(f"Exportadas {n} desambiguaciones a {out_path}", style="ok")
    say(
        f"({n}/{DISAMBIGUATION_TRAINING_THRESHOLD}) el plan sugiere ~"
        f"{DISAMBIGUATION_TRAINING_THRESHOLD} resoluciones registradas antes de "
        "considerar fine-tuning de un clasificador local (Fase 4, plan_v2.md SS8).",
        style="muted",
    )


# --------------------------------------------------------------------------- import-markdown


def _collect_memories(files: list[Path]) -> list[tuple[Path, ParsedMemory]]:
    collected: list[tuple[Path, ParsedMemory]] = []
    for f in files:
        try:
            memories = parse_markdown_file(f)
        except (OSError, UnicodeDecodeError) as exc:
            warn(f"  ! error leyendo {f}: {exc}")
            continue
        for mem in memories:
            collected.append((f, mem))
    return collected


def _ensure_context(client: MemoryClient, args: argparse.Namespace) -> None:
    existing = {c["slug"] for c in client.list_contexts()}
    if args.context in existing:
        return

    if not args.create_context:
        fail(
            f"Error: el contexto '{args.context}' no existe. Usa --create-context "
            "para crearlo (opcionalmente con --context-description)."
        )

    description = args.context_description or f"Memorias importadas desde archivos Markdown ({args.path})"
    try:
        client.create_context(args.context, args.context, "domain", description=description)
        say(f"Contexto '{args.context}' creado.", style="ok")
    except CerebroAPIError as exc:
        if exc.status_code != 409:
            fail(f"Error creando el contexto '{args.context}': {exc.detail}")


def _is_duplicate(client: MemoryClient, mem: ParsedMemory, context: str) -> bool:
    try:
        data = client.search_memories(mem.content[:500], context=context, limit=1)
    except (CerebroConnectionError, CerebroAPIError):
        return False  # don't block the import over a one-off search failure

    results = data.get("results", [])
    if not results:
        return False
    top = results[0]
    return bool(top.get("score", 0.0) >= DEDUP_SCORE_THRESHOLD and top.get("title") == mem.title)


def cmd_import_markdown(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    root = Path(args.path)
    if not root.exists():
        fail(f"Error: no existe la ruta '{root}'")

    files = iter_markdown_files(root)
    if not files:
        say(f"No se encontraron archivos .md en '{root}'", style="warning")
        return

    memories = _collect_memories(files)
    if not memories:
        say(f"Se leyeron {len(files)} archivo(s) pero no se detecto ninguna memoria importable.", style="warning")
        return

    if args.dry_run:
        say(f"[dry-run] {len(memories)} memoria(s) detectada(s) en {len(files)} archivo(s):", style="muted")
        for f, mem in memories:
            mem_type = args.type_ or mem.type
            say(f"  - [{mem_type}] \"{mem.title}\" ({len(mem.content)} chars) <- {f}")
        return

    client = client or _client()
    try:
        _ensure_context(client, args)
    except CerebroConnectionError as exc:
        _fail_request(exc)

    imported = duplicated = rejected = 0

    for f, mem in memories:
        mem_type = args.type_ or mem.type

        if _is_duplicate(client, mem, args.context):
            duplicated += 1
            say(f"  = duplicada: \"{mem.title}\"", style="warning")
            continue

        try:
            client.create_memory(
                mem.content, args.context, mem_type, title=mem.title, importance=mem.importance
            )
        except CerebroConnectionError as exc:
            rejected += 1
            say(f"  x error de conexion: \"{mem.title}\" -> {exc}", style="error")
            continue
        except CerebroAPIError as exc:
            rejected += 1
            say(f"  x rechazada ({exc.status_code}): \"{mem.title}\" -> {exc.detail}", style="error")
            continue

        imported += 1
        say(f"  + importada: \"{mem.title}\"", style="ok")

    say()
    say(
        f"Resumen: {imported} importadas, {duplicated} duplicadas (saltadas), {rejected} rechazadas.",
        style="ok" if rejected == 0 else "warning",
    )


# --------------------------------------------------------------------------- token (escopado a memory)


def cmd_token_create(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    contexts = [c.strip() for c in args.contexts.split(",") if c.strip()] if args.contexts else None

    try:
        data = client.create_token(args.name, scopes, allowed_contexts=contexts)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    contexts_desc = ", ".join(data["allowed_contexts"]) if data.get("allowed_contexts") else "todos"
    reveal(
        f"Token creado para '{data['name']}' (scopes: {', '.join(data['scopes'])}; contextos: {contexts_desc}).",
        data["token"],
        "Guarda este token ahora - cerebro-memory solo guarda su hash SHA-256 y no puede "
        "volver a mostrarlo. Usalo como CEREBRO_TOKEN o Authorization: Bearer <token> "
        "(valido solo para cerebro-memory; para un token que funcione en ambos servicios "
        "usa `cerebro token create`, sin el subcomando `memory`).",
    )


def cmd_token_list(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        tokens = client.list_tokens()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    if not tokens:
        say("(sin tokens todavia; el token root de .env sigue funcionando aparte)", style="muted")
        return

    table(
        ["name", "scopes", "contexts", "estado", "created_at"],
        [
            [
                t["name"],
                ",".join(t["scopes"]),
                ",".join(t["allowed_contexts"]) if t.get("allowed_contexts") else "*",
                "revocado" if t.get("revoked_at") else "activo",
                t["created_at"],
            ]
            for t in tokens
        ],
        styles=["label", None, None, "status", "muted"],
    )


def cmd_token_revoke(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        client.revoke_token(args.name)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    say(f"Token '{args.name}' revocado (cerebro-memory).", style="ok")
