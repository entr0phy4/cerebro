"""`cerebro flow <subcommand>` subcommands (luisjdev-pendientes/cerebro-flows): CRUD
for flow categories/definitions via `FlowsClient` (`cerebro_clients`), with no
business logic of its own beyond reading YAML from a file and formatting console
output. No commands to RUN flows (flow_start/flow_next) -- a flow is driven
by a model turn by turn via the MCP tools; it makes no sense typed by hand.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cerebro_clients import CerebroAPIError, CerebroConnectionError, FlowsClient

from cerebro_cli.console import fail, metrics, plain, say, table
from cerebro_cli.interactive import confirm


def _fail_request(exc: CerebroConnectionError | CerebroAPIError) -> None:
    if isinstance(exc, CerebroConnectionError):
        fail(f"No se pudo conectar con cerebro-flows: {exc}")
    else:
        fail(f"La API devolvio {exc.status_code}: {exc.detail}")


def _client() -> FlowsClient:
    return FlowsClient()


def _read_yaml(args: argparse.Namespace) -> str:
    path = Path(args.yaml_file)
    if not path.exists():
        fail(f"Error: no existe el archivo '{path}'")
    return path.read_text(encoding="utf-8")


def _print_flow(flow: dict) -> None:
    say(
        f"[{flow['category']}] {flow['code']} - {flow['name']}  "
        f"(v{flow['current_version']}, {flow['status']})"
    )
    say(f"  id: {flow['id']}", style="muted")


def _print_flow_list(flows: list[dict]) -> None:
    if not flows:
        say("(sin flujos)", style="muted")
        return
    table(
        ["categoria", "code", "nombre", "version", "estado"],
        [
            [f["category"], f["code"], f["name"], f"v{f['current_version']}", f["status"]]
            for f in flows
        ],
        styles=["label", "count", None, "count", "status"],
    )


# --------------------------------------------------------------------------- category


def cmd_category_create(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        category = client.create_category(args.slug, args.code, args.name or args.slug, description=args.description)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Categoria '{category['slug']}' (code={category['code']}) creada.", style="ok")


def cmd_category_list(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        categories = client.list_categories()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    if not categories:
        say("(sin categorias todavia)", style="muted")
        return
    table(
        ["slug", "code", "name", "description"],
        [[c["slug"], c["code"], c["name"], c.get("description") or ""] for c in categories],
        styles=["label", "count", None, "muted"],
    )


# --------------------------------------------------------------------------- flow definitions


def cmd_validate(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        client.validate_flow(yaml_content)
    except CerebroConnectionError as exc:
        _fail_request(exc)
    except CerebroAPIError as exc:
        fail(f"Invalido: {exc.detail}")
    say("Valido.", style="ok")


def cmd_save(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        flow = client.create_flow(args.category, yaml_content, code=args.code)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Flujo guardado: {flow['code']} (id={flow['id']}).", style="ok")


def cmd_get(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        flow = client.get_flow(args.code)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    plain(flow["yaml_content"])


def cmd_list(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        flows = client.list_flows(category=args.category, limit=args.limit, offset=args.offset)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    _print_flow_list(flows)


def cmd_update(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        flow = client.update_flow(args.code, yaml_content)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Flujo {flow['code']} actualizado a v{flow['current_version']}.", style="ok")


def cmd_delete(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    if not args.yes and not confirm(
        f"Esto borrara el flujo {args.code} (y su historial de versiones/ejecuciones). Continuar?"
    ):
        say("Cancelado.", style="muted")
        return

    try:
        client.delete_flow(args.code)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Flujo {args.code} borrado.", style="ok")


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    metrics(
        "Estadisticas de cerebro-flows",
        [
            ("categorias", data["categories"]),
            ("flujos", data["flows"]),
            ("runs", data["runs"]),
        ],
    )
