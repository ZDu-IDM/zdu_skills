"""Re-scan every registered repo, then rebuild the combined ``all_skills.html``.

Reads ``repos/registry.json`` (maintained by ``register_repo.py``), re-scans each
entry from its stored input, refreshes that repo's ``skills.json`` and per-repo
view, updates its ``last_scanned_at``, and finally regenerates the aggregate page.

A failure on one repo (network error, deleted local path, …) is reported and
skipped so the rest still refresh.

Usage:
    python rescan_all.py
    python rescan_all.py --repos-dir repos --no-view   # skip per-repo HTML pages
    python rescan_all.py --no-aggregate                # refresh scans only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import register_repo
import registry
import generate_all_skills_view
import generate_skills_view
from generate_all_skills_view import DEFAULT_ALL_VIEW
from scan_repo import DEFAULT_REPOS_DIR, ScanError


def rescan_all(
    repos_dir: str = DEFAULT_REPOS_DIR,
    make_view: bool = True,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Re-scan every registered repo. Returns ``(succeeded_names, failures)``.

    ``failures`` is a list of ``(name, error_message)``.
    """
    entries = registry.list_entries(repos_dir)
    succeeded: list[str] = []
    failures: list[tuple[str, str]] = []

    for entry in entries:
        name = entry.get("name", "?")
        ref_input = entry.get("input")
        if not ref_input:
            failures.append((name, "no stored input to re-scan"))
            continue
        try:
            print(f"Re-scanning {name} ({ref_input}) …", file=sys.stderr)
            # record=True refreshes the registry entry's last_scanned_at too.
            register_repo.register_repo(
                ref_input,
                repos_dir=repos_dir,
                make_view=make_view,
                record=True,
            )
            succeeded.append(name)
        except ScanError as exc:
            print(f"  ! {name}: {exc}", file=sys.stderr)
            failures.append((name, str(exc)))

    return succeeded, failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-scan all registered repos and rebuild the combined HTML page."
    )
    parser.add_argument(
        "--repos-dir", default=DEFAULT_REPOS_DIR,
        help=f"Base directory holding the registry and scans (default: {DEFAULT_REPOS_DIR}).",
    )
    parser.add_argument(
        "--no-view", action="store_true",
        help="Skip regenerating each repo's own repo_skills.html.",
    )
    parser.add_argument(
        "--no-aggregate", action="store_true",
        help="Refresh per-repo scans only; don't rebuild all_skills.html.",
    )
    args = parser.parse_args(argv)

    entries = registry.list_entries(args.repos_dir)
    if not entries:
        print(
            f"No repos registered in {registry.registry_path(args.repos_dir)}. "
            f"Run register_repo.py first.",
            file=sys.stderr,
        )
        return 1

    succeeded, failures = rescan_all(
        repos_dir=args.repos_dir,
        make_view=not args.no_view,
    )

    print(f"Re-scanned {len(succeeded)}/{len(entries)} repo(s).", file=sys.stderr)
    for name, err in failures:
        print(f"  failed: {name} — {err}", file=sys.stderr)

    if not args.no_aggregate:
        data = generate_all_skills_view.aggregate(args.repos_dir)
        out_path = generate_skills_view.write_html(
            data, Path(args.repos_dir) / DEFAULT_ALL_VIEW,
        )
        print(
            f"Rebuilt {data['count']} skill(s) across {len(data['repos'])} repo(s) "
            f"-> {out_path}.",
            file=sys.stderr,
        )

    # Non-zero exit if every repo failed, so callers/CI can detect a total bust.
    return 1 if failures and not succeeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
