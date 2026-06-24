"""Track which repos are registered, in ``repos/registry.json``.

The registry is the source of truth for *which* repos to (re)scan. Each entry
records enough to re-run a scan unattended: the original ``--repo`` input (which
preserves a pinned ``/tree/<ref>``), plus derived metadata for display.

``register_repo.py`` adds/updates an entry after each successful scan;
``rescan_all.py`` reads the registry to re-scan everything. This module also has a
tiny CLI for inspecting/pruning the registry:

    python registry.py list
    python registry.py remove <repo-name>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scan_repo import DEFAULT_REPOS_DIR

REGISTRY_NAME = "registry.json"
REGISTRY_SCHEMA = 1


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registry_path(repos_dir: str | Path = DEFAULT_REPOS_DIR) -> Path:
    return Path(repos_dir) / REGISTRY_NAME


def load_registry(repos_dir: str | Path = DEFAULT_REPOS_DIR) -> dict:
    """Load the registry, returning a fresh empty one if it doesn't exist yet."""
    path = registry_path(repos_dir)
    if not path.exists():
        return {"schema_version": REGISTRY_SCHEMA, "repos": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("schema_version", REGISTRY_SCHEMA)
    data.setdefault("repos", [])
    return data


def save_registry(registry: dict, repos_dir: str | Path = DEFAULT_REPOS_DIR) -> Path:
    path = registry_path(repos_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def register(
    input_ref: str,
    data: dict,
    repos_dir: str | Path = DEFAULT_REPOS_DIR,
) -> dict:
    """Add or update the registry entry for a just-scanned repo.

    ``data`` is the scan result from ``scan_repo.scan_repo``. Dedup is by the repo
    folder name; an existing entry keeps its ``added_at`` and refreshes the rest.
    For local repos the stored ``input`` is normalized to the absolute path so
    re-scans work from any working directory.
    """
    registry = load_registry(repos_dir)
    name = data["repo"]
    local = bool(data.get("local"))
    stored_input = data.get("source") if local else input_ref

    existing = next((e for e in registry["repos"] if e.get("name") == name), None)
    entry = existing or {"added_at": _now()}
    entry.update({
        "name": name,
        "input": stored_input,
        "source": data.get("source", name),
        "local": local,
        "ref": data.get("ref", ""),
        "last_scanned_at": data.get("scanned_at") or _now(),
    })
    if existing is None:
        registry["repos"].append(entry)

    registry["repos"].sort(key=lambda e: e.get("name", "").lower())
    save_registry(registry, repos_dir)
    return entry


def unregister(name: str, repos_dir: str | Path = DEFAULT_REPOS_DIR) -> bool:
    """Remove the entry with the given repo name. Returns True if one was removed."""
    registry = load_registry(repos_dir)
    before = len(registry["repos"])
    registry["repos"] = [e for e in registry["repos"] if e.get("name") != name]
    removed = len(registry["repos"]) < before
    if removed:
        save_registry(registry, repos_dir)
    return removed


def list_entries(repos_dir: str | Path = DEFAULT_REPOS_DIR) -> list[dict]:
    return load_registry(repos_dir)["repos"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or prune the repo registry.")
    parser.add_argument(
        "--repos-dir", default=DEFAULT_REPOS_DIR,
        help=f"Base directory holding the registry (default: {DEFAULT_REPOS_DIR}).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List registered repos.")
    rm = sub.add_parser("remove", help="Remove a repo from the registry (by name).")
    rm.add_argument("name", help="Repo folder name to remove.")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        entries = list_entries(args.repos_dir)
        if not entries:
            print(f"No repos registered in {registry_path(args.repos_dir)}.", file=sys.stderr)
            return 0
        for e in entries:
            kind = "local" if e.get("local") else "github"
            print(f"  {e['name']:<28} [{kind}] {e.get('input')}  (ref: {e.get('ref')}, "
                  f"last scanned: {e.get('last_scanned_at')})")
        print(f"\n{len(entries)} repo(s) registered.", file=sys.stderr)
        return 0

    if args.cmd == "remove":
        if unregister(args.name, args.repos_dir):
            print(f"Removed '{args.name}' from the registry.", file=sys.stderr)
            return 0
        print(f"No registry entry named '{args.name}'.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
