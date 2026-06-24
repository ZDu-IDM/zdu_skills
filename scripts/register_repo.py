"""Register a repo: scan it for Claude skills, then build its HTML view.

This is the main entry point. It wires the two single-purpose scripts together:

    1. ``scan_repo.scan_repo()``        -> writes repos/<repo>/skills.json
    2. ``generate_skills_view``         -> writes repos/<repo>/repo_skills.html

Both halves remain usable on their own (``scan_repo.py`` / ``generate_skills_view.py``);
this just runs them back-to-back in-process so the scanned dict is handed straight
to the renderer without a redundant JSON round-trip.

This is step one of the larger app: once several repos are registered (each with
its own skills.json under repos/), a future aggregator can read them all and build
one combined page listing every skill across every repo.

Usage:
    python register_repo.py --repo anthropics/skills
    python register_repo.py --repo https://github.com/owner/repo --repos-dir repos
    python register_repo.py --repo C:\\work\\AI\\starsim_ai     # local clone
    python register_repo.py --repo anthropics/skills --no-view  # scan only

Set GITHUB_TOKEN to raise the API rate limit (60 -> 5000 req/hr) and scan private repos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import scan_repo
import generate_skills_view
import registry
from scan_repo import DEFAULT_MAX_FILE_BYTES, DEFAULT_REPOS_DIR, ScanError


def register_repo(
    repo_input: str,
    repos_dir: str = DEFAULT_REPOS_DIR,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    make_view: bool = True,
    record: bool = True,
) -> tuple[dict, Path, Path | None]:
    """Scan ``repo_input``, render its view, and record it in the registry.

    Returns ``(data, json_path, html_path_or_None)``.
    """
    data = scan_repo.scan_repo(repo_input, max_file_bytes)
    json_path = scan_repo.write_skills_json(data, repos_dir)

    html_path: Path | None = None
    if make_view:
        html_path = json_path.parent / generate_skills_view.DEFAULT_VIEW_NAME
        generate_skills_view.write_html(data, html_path)

    if record:
        registry.register(repo_input, data, repos_dir)

    return data, json_path, html_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a repo for Claude skills and build its self-contained HTML view."
    )
    parser.add_argument(
        "--repo", required=True,
        help=(
            "GitHub repo ('owner/repo', a URL, or a '/tree/<ref>' URL to pin a "
            "branch/tag) OR a path to an already-cloned local directory."
        ),
    )
    parser.add_argument(
        "--repos-dir", default=DEFAULT_REPOS_DIR,
        help=f"Base directory for per-repo output (default: {DEFAULT_REPOS_DIR}).",
    )
    parser.add_argument(
        "--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES,
        help="Skip embedding files larger than this (still listed). Default: 5 MiB.",
    )
    parser.add_argument(
        "--no-view", action="store_true",
        help="Only scan and write skills.json; skip generating the HTML page.",
    )
    parser.add_argument(
        "--no-register", action="store_true",
        help="Scan without recording the repo in repos/registry.json.",
    )
    args = parser.parse_args(argv)

    try:
        print(f"Scanning {args.repo} …", file=sys.stderr)
        data, json_path, html_path = register_repo(
            args.repo,
            repos_dir=args.repos_dir,
            max_file_bytes=args.max_file_bytes,
            make_view=not args.no_view,
            record=not args.no_register,
        )
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Found {data['count']} skill(s) in {data.get('source') or data['repo']} "
        f"(ref: {data['ref']}).",
        file=sys.stderr,
    )
    print(f"  scan : {json_path}", file=sys.stderr)
    if html_path:
        print(f"  view : {html_path}", file=sys.stderr)
    if data["truncated"]:
        print("Note: GitHub truncated the file tree; some skills may be missing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
