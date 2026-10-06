"""`cerebro docs <subcommand>` subcommands (ecosistema-cerebro.md SS11): categories,
full-document CRUD, partial per-section patching and stats -- all via
`DocsClient` (`cerebro_clients`), with no business logic of its own beyond reading
content from a file/stdin and formatting console output.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cerebro_clients import CerebroAPIError, CerebroConnectionError, DocsClient
from cerebro_memory.markdown_importer import iter_markdown_files

from cerebro_cli.console import fail, metrics, plain, say, table
from cerebro_cli.interactive import confirm, is_interactive, prompt_path


def _fail_request(exc: CerebroConnectionError | CerebroAPIError) -> None:
    if isinstance(exc, CerebroConnectionError):
        fail(f"No se pudo conectar con cerebro-docs: {exc}")
    else:
        fail(f"La API devolvio {exc.status_code}: {exc.detail}")

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _client() -> DocsClient:
    return DocsClient()


def _read_file(path: Path) -> str:
    if not path.exists():
        fail(f"Error: no existe el archivo '{path}'")
    return path.read_text(encoding="utf-8")


def _read_content(args: argparse.Namespace) -> str:
    """Markdown content from --content-file, a prompted path, or stdin.

    Full documents aren't practical as a single command-line argument. On a
    TTY with no file, ask for a path instead of failing.
    """
    if args.content_file:
        return _read_file(Path(args.content_file))
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if is_interactive():
        return _read_file(Path(prompt_path("Ruta del archivo Markdown")))
    fail("Error: pasa --content-file o redirige el contenido por stdin.")


def _print_document(doc: dict) -> None:
    say(f"[{doc['category']}/{doc['slug']}] {doc['title']}")
    say(f"  id: {doc['id']}", style="muted")
    say(
        f"  creado por: {doc.get('created_by') or 'unknown'}  actualizado: {doc['updated_at']}",
        style="muted",
    )


def _print_document_list(docs: list[dict]) -> None:
    if not docs:
        say("(sin documentos)", style="muted")
        return
    show_score = any(d.get("score") is not None for d in docs)
    headers = ["ruta", "titulo", "score"] if show_score else ["ruta", "titulo"]
    column_styles = ["label", None, "count"] if show_score else ["label", None]
    rows = []
    for d in docs:
        row = [f"{d['category']}/{d['slug']}", d["title"]]
        if show_score:
            row.append(f"{d['score']:.3f}" if d.get("score") is not None else "")
        rows.append(row)
    table(headers, rows, styles=column_styles)


# --------------------------------------------------------------------------- category


def cmd_category_create(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        category = client.create_category(
            args.slug,
            args.name or args.slug,
            description=args.description,
            hidden=args.hidden,
            locked=args.locked,
        )
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    flags = " (oculta, bloqueada)" if category["locked"] else " (oculta)" if category["hidden"] else ""
    say(f"Categoria '{category['slug']}' creada{flags}.", style="ok")


def cmd_category_list(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        categories = client.list_categories()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    if not categories:
        say("(sin categorias todavia)", style="muted")
        return
    table(
        ["slug", "name", "description"],
        [[c["slug"], c["name"], c.get("description") or ""] for c in categories],
        styles=["label", None, "muted"],
    )


def cmd_category_rename(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    hidden = True if args.hidden else False if args.visible else None
    try:
        category = client.update_category(
            args.slug, new_slug=args.new_slug, name=args.name, description=args.description, hidden=hidden
        )
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Categoria '{args.slug}' -> '{category['slug']}'.", style="ok")


def cmd_category_delete(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        result = client.delete_category(args.slug, force=args.force)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Categoria '{args.slug}' borrada (documentos borrados: {result['documents_deleted']}).", style="ok")


# --------------------------------------------------------------------------- documents


def cmd_save(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    content = _read_content(args)
    try:
        document = client.create_document(args.title, content, args.category, slug=args.slug)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Documento guardado en {document['category']}/{document['slug']} (id={document['id']}).", style="ok")


def cmd_get(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.get_document(args.category, args.slug)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    plain(document["content"])


def cmd_list(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        if args.archived:
            documents = client.list_archived_documents(category=args.category, limit=args.limit, offset=args.offset)
        else:
            documents = client.list_documents(category=args.category, limit=args.limit, offset=args.offset)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    _print_document_list(documents)


def cmd_search(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        documents = client.list_documents(category=args.category, q=args.query, limit=args.limit, offset=args.offset)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    _print_document_list(documents)


def cmd_update(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    content = _read_content(args)
    try:
        document = client.update_document(args.document_id, args.title, content, args.category, slug=args.slug)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Documento {document['id']} actualizado ({document['category']}/{document['slug']}).", style="ok")


def cmd_patch_section(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    body = ""
    if args.operation != "delete":
        if args.body_file:
            body = _read_file(Path(args.body_file))
        elif args.body is not None:
            body = args.body
        elif not sys.stdin.isatty():
            body = sys.stdin.read()
        elif is_interactive():
            body = _read_file(Path(prompt_path("Ruta del archivo con el contenido del parche")))

    try:
        document = client.patch_section(
            args.document_id,
            args.heading,
            args.operation,
            body=body,
            create_if_missing=args.create_if_missing,
            new_heading_level=args.new_heading_level,
        )
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(
        f"Seccion '{args.heading}' ({args.operation}) aplicada a {document['category']}/{document['slug']}.",
        style="ok",
    )


def cmd_archive(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.archive_document(args.document_id)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Documento {document['id']} archivado ({document['category']}/{document['slug']}).", style="ok")


def cmd_unarchive(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.unarchive_document(args.document_id)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Documento {document['id']} desarchivado ({document['category']}/{document['slug']}).", style="ok")


def cmd_history(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        versions = client.get_document_versions(args.document_id)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    if not versions:
        say("(sin versiones anteriores)", style="muted")
        return
    table(
        ["version", "categoria", "titulo", "creado", "preview"],
        [
            [
                f"v{v['version_number']}",
                v["category"],
                v["title"],
                v["created_at"],
                v["content"].splitlines()[0][:80] if v["content"].strip() else "",
            ]
            for v in versions
        ],
        styles=["count", "label", None, "muted", "muted"],
    )


def cmd_delete(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    if not args.yes and not confirm(
        f"Esto borrara el documento {args.document_id} (y su historial de versiones). Continuar?"
    ):
        say("Cancelado.", style="muted")
        return

    try:
        client.delete_document(args.document_id)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)
    say(f"Documento {args.document_id} borrado.", style="ok")


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    metrics(
        "Estadisticas de cerebro-docs",
        [
            ("categorias", data["categories"]),
            ("documentos", data["documents"]),
            ("versiones", data["versions"]),
        ],
    )


# --------------------------------------------------------------------------- import-markdown


def _derive_title_and_slug(path: Path, text: str) -> tuple[str, str]:
    """Title = the file's first '# heading' if present, otherwise the file
    name. Slug = sanitized file name, passed EXPLICITLY (not derived from the
    title) so it stays stable and traceable to the source file -- see
    luisjdev-pendientes/ecosistema-cerebro, "Bulk markdown importer"."""
    m = _TITLE_RE.search(text)
    title = m.group(1) if m else path.stem
    slug = path.stem.lower().replace("_", "-")
    return title, slug


def cmd_import_markdown(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    root = Path(args.path)
    if not root.exists():
        fail(f"Error: no existe la ruta '{root}'")

    files = iter_markdown_files(root)
    if not files:
        say(f"No se encontraron archivos .md en '{root}'", style="warning")
        return

    if args.dry_run:
        say(f"[dry-run] {len(files)} archivo(s) en '{root}' -> categoria '{args.category}':", style="muted")
        for f in files:
            title, slug = _derive_title_and_slug(f, f.read_text(encoding="utf-8"))
            say(f"  - [{args.category}/{slug}] \"{title}\" <- {f}")
        return

    client = client or _client()
    imported = skipped = updated = rejected = 0

    for f in files:
        text = f.read_text(encoding="utf-8")
        title, slug = _derive_title_and_slug(f, text)

        existing: dict | None = None
        try:
            existing = client.get_document(args.category, slug)
        except CerebroAPIError as exc:
            if exc.status_code != 404:
                rejected += 1
                say(
                    f"  x error verificando duplicado ({exc.status_code}): \"{title}\" -> {exc.detail}",
                    style="error",
                )
                continue
        except CerebroConnectionError as exc:
            _fail_request(exc)

        if existing is not None and not args.update:
            skipped += 1
            say(f"  = ya existe, omitido: \"{title}\" ({args.category}/{slug})", style="warning")
            continue

        try:
            if existing is not None:
                client.update_document(existing["id"], title, text, args.category, slug=slug)
                updated += 1
                say(f"  ~ actualizado: \"{title}\" ({args.category}/{slug})", style="ok")
            else:
                client.create_document(title, text, args.category, slug=slug)
                imported += 1
                say(f"  + importado: \"{title}\" ({args.category}/{slug})", style="ok")
        except CerebroConnectionError as exc:
            rejected += 1
            say(f"  x error de conexion: \"{title}\" -> {exc}", style="error")
        except CerebroAPIError as exc:
            rejected += 1
            say(f"  x rechazado ({exc.status_code}): \"{title}\" -> {exc.detail}", style="error")

    say()
    say(
        f"Importados: {imported}  Actualizados: {updated}  "
        f"Omitidos (ya existian): {skipped}  Rechazados: {rejected}",
        style="ok" if rejected == 0 else "warning",
    )
