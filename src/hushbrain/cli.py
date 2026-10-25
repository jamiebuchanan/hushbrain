"""Command line: serve the MCP server, index a vault, set up the database, check the policy."""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hushbrain import __version__
from hushbrain.config import ConfigError, Settings, load_settings
from hushbrain.db import Database, SchemaError, init_schema, verify_schema
from hushbrain.embeddings import make_embedder
from hushbrain.indexer import Indexer, watch
from hushbrain.notes import parse_note
from hushbrain.policy import PolicyError, load, load_or_builtin
from hushbrain.store import MemoryStore


def setup_logging(settings: Settings) -> None:
    """Always log to stderr: on the stdio transport, stdout belongs to the MCP protocol."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(settings.log_file, maxBytes=10 * 1024 * 1024, backupCount=3))
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_policy(settings: Settings, *, strict: bool):
    """Load the configured policy.

    Readers (the server) may fall back to the built-in policy when the file is broken, because
    they only apply `default_visible` to tiers that are already stored. Writers and reporters
    (the indexer, audit) must not: the built-in policy has no project floors, so indexing under
    it would re-store a floored note at its own, lower label and the server would then show it.
    With strict=True a configured but unreadable or invalid policy is an error (exit 2).
    """
    if strict and settings.policy_path is not None:
        return load(settings.policy_path)
    return load_or_builtin(settings.policy_path)


def _build(settings: Settings, *, strict_policy: bool = False):
    policy = _load_policy(settings, strict=strict_policy)
    db = Database(settings.dsn)
    embedder = make_embedder(settings)
    return policy, db, embedder, MemoryStore(db, policy, settings, embedder)


# ---- commands ----------------------------------------------------------------------


def cmd_serve(settings: Settings, _args: argparse.Namespace) -> int:
    from hushbrain.server import build_server

    policy, db, _embedder, store = _build(settings)
    verify_schema(db, settings.embed_dim)
    logging.getLogger("hushbrain").info(
        "serving; default-visible tiers: %s; sensitive lock: %s",
        ",".join(policy.default_visible), settings.lock_sensitive,
    )
    build_server(store).run(transport="stdio")
    return 0


def cmd_init_db(settings: Settings, _args: argparse.Namespace) -> int:
    db = Database(settings.dsn)
    init_schema(db, settings.embed_dim)
    print(f"store ready (embed_dim={settings.embed_dim})")
    return 0


def cmd_index(settings: Settings, args: argparse.Namespace) -> int:
    policy, db, embedder, store = _build(settings, strict_policy=True)
    verify_schema(db, settings.embed_dim)
    indexer = Indexer(settings, policy, store, embedder)
    if args.watch:
        return watch(indexer)
    res = indexer.initial_scan()
    print(f"{res.files} files, embedded {res.embedded}, unchanged {res.skipped_embeds}, failed {res.failed}")
    return 1 if res.failed else 0


def cmd_check_policy(settings: Settings, args: argparse.Namespace) -> int:
    path = args.path or settings.policy_path
    if path is None:
        print("no policy file given (pass a path, or set HUSHBRAIN_POLICY_PATH); the built-in policy applies")
        return 2
    p = load(path)
    print("policy ok:", p.source)
    print("levels:", list(p.levels), "default_visible:", list(p.default_visible),
          "default_for_missing:", p.default_for_missing)
    print("project floors (counts only):", dict(Counter(p.project_floors.values())))
    r = p.export
    print("export: allow_tiers", list(r.allow_tiers), "deny_types", len(r.deny_types),
          "deny_projects", len(r.deny_projects), "blocklist", "set" if r.blocklist else "empty")
    return 0


def cmd_audit(settings: Settings, args: argparse.Namespace) -> int:
    """Read a vault and report how it would be labelled and exported. No database needed."""
    policy = _load_policy(settings, strict=True)
    nodes = Path(args.vault).expanduser() / settings.nodes_subdir
    if not nodes.is_dir():
        print(f"no nodes directory at {nodes}", file=sys.stderr)
        return 2
    tiers: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    export: Counter[str] = Counter()
    problems: list[tuple[str, str]] = []
    unparsed = 0
    for path in sorted(nodes.glob("*.md")):
        note = parse_note(path, policy)
        if note is None:
            unparsed += 1
            problems.append((path.name, "unparseable"))
            continue
        tiers[note.sensitivity] += 1
        reasons[note.sensitivity_reason] += 1
        if note.sensitivity_reason != "explicit":
            problems.append((path.name, note.sensitivity_reason))
        ok, why = policy.export_decision({
            "id": note.node_id, "type": note.ntype, "project_id": note.project_id,
            "sensitivity": note.sensitivity, "title": note.title,
            "summary": note.summary, "body": note.body,
        })
        export["admitted" if ok else f"refused:{why}"] += 1
    print(f"notes: {sum(tiers.values())}  unparseable: {unparsed}")
    print("by stored tier:", dict(tiers))
    print("how the tier was reached:", dict(reasons))
    print("export gate:", dict(export))
    if args.list and problems:
        print("\nneeds attention (file, reason):")
        for name, why in problems:
            print(f"  {name}  {why}")
    return 0


# ---- entry point -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hushbrain", description=__doc__)
    parser.add_argument("--version", action="version", version=f"hushbrain {__version__}")
    parser.add_argument("--config", help="path to a hushbrain.toml (or set HUSHBRAIN_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the MCP server on stdio").set_defaults(fn=cmd_serve)
    sub.add_parser("init-db", help="create the tables (needs the vector extension)").set_defaults(fn=cmd_init_db)

    p_index = sub.add_parser("index", help="index the vault once, or keep watching it")
    p_index.add_argument("--watch", action="store_true", help="keep running and follow changes")
    p_index.set_defaults(fn=cmd_index)

    p_pol = sub.add_parser("check-policy", help="validate a policy file, print counts only")
    p_pol.add_argument("path", nargs="?")
    p_pol.set_defaults(fn=cmd_check_policy)

    p_aud = sub.add_parser("audit", help="report how a vault would be labelled and exported")
    p_aud.add_argument("vault")
    p_aud.add_argument("--list", action="store_true", help="name the files that need attention")
    p_aud.set_defaults(fn=cmd_audit)

    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        setup_logging(settings)
        return args.fn(settings, args)
    except (ConfigError, PolicyError, SchemaError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
