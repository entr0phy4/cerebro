"""Ecosystem-level commands, with no module prefix (ecosistema-cerebro.md SS11):

- `cerebro backup`: streams `POST /backup` from `cerebro-auth` (admin-only) to a
  local file -- a full `pg_dump` of the whole shared Postgres instance (all 4
  schemas), run server-side and piped straight through, same as a browser
  download. Works against any deployment (local or remote) the caller has an
  admin token for, unlike the old `docker compose exec` mechanism this replaced,
  which only worked against a local checkout with Docker running.
- `cerebro restore`: unchanged -- still `psql` via `docker compose exec` against
  a local checkout. Restore was explicitly left out of the `cerebro-auth`
  `/backup` design (extraction only, see `luisjdev-pendientes/ecosistema-cerebro`).
- `cerebro token create/revoke`: token management for the whole ecosystem, via
  `AuthClient` against the single `cerebro-auth` service (SS13). This used
  to orchestrate one secret across cerebro-memory and cerebro-docs independently,
  with local pending-retry state for partial failures (see git history / `tokens.py`'s
  docstring) -- now that there's exactly one service to talk to, it's a single call
  that either succeeds or fails, nothing to retry locally.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime
from pathlib import Path

from cerebro_clients import AuthClient, CerebroAPIError, CerebroConnectionError

from cerebro_cli.console import fail, reveal, say

def _find_repo_root() -> Path | None:
    """Best-effort monorepo root (parents[4] of this file -- where compose.yaml
    lives), for `cerebro restore`'s `docker compose exec`, which only makes sense
    against a local checkout. None when there's no such ancestor -- the standalone
    PyInstaller binary (see .github/workflows/release-cli.yml) extracts this file
    into a temp directory with just a few levels of parents, so the plain
    `.parents[4]` indexing used to crash at import time before any command even
    ran (found live: `cerebro --help` in the packaged binary raised `IndexError`).
    Callers that need a real checkout must handle None explicitly instead of
    relying on this crashing loudly for them.
    """
    parents = Path(__file__).resolve().parents
    return parents[4] if len(parents) > 4 else None


REPO_ROOT = _find_repo_root()

# `cerebro backup`'s default output directory. Not tied to REPO_ROOT (backup talks
# straight to cerebro-auth's HTTP API now, no docker/repo dependency at all -- see
# module docstring) -- uses the user's home directory so it also works from the
# standalone binary, with no checkout in sight. DELIBERATELY still outside any repo
# tree if one happens to exist nearby -- ecosistema-cerebro.md SS15, audit
# criterion: a full dump (includes document content, which SS2/SS9 clarify may
# carry secrets pasted in by mistake) must not be able to end up committed by
# accident nor live under a versioned directory.
DEFAULT_BACKUP_DIR = Path.home() / "cerebro-backups"

POSTGRES_USER = "knowledgeos"  # service/user/DB name in compose.yaml - unchanged (SS5)
POSTGRES_DB = "knowledgeos"


def _fail_request(exc: CerebroConnectionError | CerebroAPIError) -> None:
    if isinstance(exc, CerebroConnectionError):
        fail(f"No se pudo conectar con cerebro-auth: {exc}")
    else:
        fail(f"La API devolvio {exc.status_code}: {exc.detail}")


# --------------------------------------------------------------------------- backup / restore


def cmd_backup(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    out_dir = Path(args.output) if args.output else DEFAULT_BACKUP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"cerebro-{timestamp}.sql"

    # A full pg_dump can run well past the default 30s client timeout on a
    # database of any real size - the request is a single streamed download, not
    # a quick CRUD call, so it gets a generous timeout of its own.
    client = client or AuthClient(timeout=1800.0)
    say(f"Descargando backup de {client.base_url} -> {out_file}")
    try:
        client.backup(out_file)
    except (CerebroConnectionError, CerebroAPIError) as exc:
        out_file.unlink(missing_ok=True)
        _fail_request(exc)

    # The dump contains all content of all 4 schemas (includes cerebro-docs
    # documents, which by design may carry secrets pasted in by mistake - see
    # ecosistema-cerebro.md SS9) - it must never end up readable by
    # other system users. Best-effort: no-op on Windows.
    try:
        os.chmod(out_file, 0o600)
    except OSError:
        pass

    size = out_file.stat().st_size
    say(
        f"Backup guardado en {out_file} ({size} bytes) - cubre memory, docs, flows y auth "
        "(un solo Postgres compartido).",
        style="ok",
    )


def cmd_restore(args: argparse.Namespace) -> None:
    if REPO_ROOT is None:
        fail(
            "Error: 'cerebro restore' necesita correr desde un checkout del repo "
            "cerebro (usa 'docker compose exec' contra compose.yaml) -- no disponible "
            "desde el binario standalone."
        )

    path = Path(args.file)
    if not path.exists():
        fail(f"Error: no existe el archivo '{path}'")

    if not args.yes:
        answer = input(
            f"Esto SOBREESCRIBIRA la base de datos '{POSTGRES_DB}' (schemas cerebro_memory y "
            f"cerebro_docs) con el contenido de '{path}'. Esta accion es DESTRUCTIVA e "
            "irreversible.\nEscribe 'yes' para continuar: "
        )
        if answer.strip().lower() != "yes":
            say("Cancelado.", style="muted")
            return

    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB]
    say(f"Ejecutando: {' '.join(cmd)} < {path}")
    try:
        with open(path, "rb") as fh:
            result = subprocess.run(cmd, cwd=REPO_ROOT, stdin=fh, stderr=subprocess.PIPE)
    except FileNotFoundError:
        fail("Error: no se encontro el comando 'docker'. ¿Docker Desktop esta corriendo?")

    if result.returncode != 0:
        fail(f"Error en restore (exit {result.returncode}): {result.stderr.decode(errors='replace')}")

    say("Restore completado.", style="ok")


# --------------------------------------------------------------------------- token (cerebro-auth)


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _auth_client() -> AuthClient:
    return AuthClient()


def cmd_token_create(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    """Creates a token in cerebro-auth, the ecosystem's single source of truth for
    identity and tokens (SS13, updated). One call, one response -- no more
    per-service partial failure to reconcile (see `tokens.py`'s docstring for what
    this replaced).

    `--user` and `--access-level` are mutually exclusive (matches the server's own
    `CHECK ((user_id IS NULL) = (access_level IS NOT NULL))`): a user-owned token
    inherits its level from the user (or their groups), a service/root-adjacent
    token (no `--user`) must state its own level explicitly. Likewise
    `allowed_modules` is mandatory for a service token (no `--user`) and optional --
    narrowing, never widening -- for a user-owned one.
    """
    if args.user and args.access_level:
        fail(
            "Error: no pases --user y --access-level juntos -- el nivel de un token de "
            "usuario lo define el usuario (o sus grupos), nunca el propio token."
        )
    if not args.user and not args.access_level:
        fail("Error: pasa --user <nombre> o --access-level user|owner|admin.")

    modules = _split_csv(args.modules)
    if not args.user and not modules:
        fail(
            "Error: un token sin --user (de servicio) necesita --modules explicito "
            "(no puede heredarlo de ningun usuario)."
        )

    scopes = _split_csv(args.scopes) or []

    module_scopes: dict[str, dict[str, list[str]]] = {}
    if args.memory_contexts is not None:
        module_scopes["memory"] = {"contexts": _split_csv(args.memory_contexts) or []}
    if args.docs_categories is not None:
        module_scopes["docs"] = {"categories": _split_csv(args.docs_categories) or []}
    if args.flows_categories is not None:
        module_scopes["flows"] = {"categories": _split_csv(args.flows_categories) or []}

    client = client or _auth_client()
    try:
        data = client.create_token(
            args.name,
            scopes,
            allowed_modules=modules,
            module_scopes=module_scopes or None,
            user=args.user,
            access_level=args.access_level,
        )
    except (CerebroConnectionError, CerebroAPIError) as exc:
        _fail_request(exc)

    reveal(
        f"Token '{data.get('name', args.name)}' creado (scopes: {', '.join(data.get('scopes', scopes))}).",
        data["token"],
        "Guarda este token ahora - cerebro-auth solo guarda su hash y no puede volver a "
        "mostrarlo. Usalo como CEREBRO_TOKEN (valido en todo el ecosistema).",
    )


def cmd_token_revoke(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    """Revokes a token by name in cerebro-auth. A 404 (already revoked/nonexistent)
    counts as success -- the desired state ("not active") is already met, same
    idempotent-revoke behavior as before the unification (SS13)."""
    client = client or _auth_client()

    try:
        client.revoke_token(args.name)
    except CerebroConnectionError as exc:
        _fail_request(exc)
    except CerebroAPIError as exc:
        if exc.status_code == 404:
            say(f"Token '{args.name}' ya no estaba activo.", style="warning")
            return
        _fail_request(exc)

    say(f"Token '{args.name}' revocado.", style="ok")
