"""Aggregate every ``repos/<repo>/skills.json`` into one combined HTML page.

After several repos have been registered (each with its own ``skills.json`` under
``repos/`` — see ``register_repo.py``), this reads them all and renders a single
self-contained page listing every skill across every repo. Because each
``skills.json`` already embeds its file bytes and absolute URLs, no re-scanning and
no network access are needed: this is a pure merge + render step.

Each skill is stamped with the ``repo`` (and ``source``) it came from, so the page
shows a repo badge per card and offers a "Repo" filter (rendered by the shared,
repo-aware template in ``generate_skills_view.py``).

Usage:
    python generate_all_skills_view.py                       # scans ./repos
    python generate_all_skills_view.py --repos-dir repos -o all_skills.html
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import generate_skills_view
import registry
from scan_repo import DEFAULT_REPOS_DIR, SCHEMA_VERSION, SKILLS_JSON

DEFAULT_ALL_VIEW = "all_skills.html"


def find_skills_json(repos_dir: str | Path) -> list[Path]:
    """Return every ``<repos_dir>/*/skills.json`` path, sorted by repo folder."""
    return sorted(Path(repos_dir).glob(f"*/{SKILLS_JSON}"))


def registered_skills_json(repos_dir: str | Path) -> list[Path]:
    """Return the ``skills.json`` path for each *registered* repo, in name order.

    Registered repos with no scan on disk yet are warned about and skipped.
    """
    base = Path(repos_dir)
    paths: list[Path] = []
    for entry in registry.list_entries(repos_dir):
        path = base / entry["name"] / SKILLS_JSON
        if path.exists():
            paths.append(path)
        else:
            print(f"Registered repo '{entry['name']}' has no {SKILLS_JSON} yet "
                  f"— run rescan_all.py. Skipping.", file=sys.stderr)
    return sorted(paths)


def aggregate(
    repos_dir: str | Path = DEFAULT_REPOS_DIR,
    registered_only: bool = True,
) -> dict:
    """Merge per-repo skills.json files into one combined dataset.

    By default only repos listed in ``repos/registry.json`` are included, so stray
    scan folders don't leak into the page; pass ``registered_only=False`` to fall
    back to discovering every ``skills.json`` on disk.

    The returned dict has the same shape the renderer expects, plus
    ``aggregate: True`` and a ``repos`` summary list. Each skill gains ``repo``,
    ``source`` and ``repo_local`` fields so it stays attributable in the page.
    """
    paths = registered_skills_json(repos_dir) if registered_only else find_skills_json(repos_dir)
    repos: list[dict] = []
    skills: list[dict] = []
    truncated_any = False

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Skipping {path}: {exc}", file=sys.stderr)
            continue

        repo = data.get("repo") or path.parent.name
        source = data.get("source") or repo
        local = bool(data.get("local"))
        truncated_any = truncated_any or bool(data.get("truncated"))

        repo_skills = data.get("skills", [])
        repos.append({
            "repo": repo,
            "owner": data.get("owner", ""),
            "source": source,
            "ref": data.get("ref", ""),
            "local": local,
            "count": data.get("count", len(repo_skills)),
            "scanned_at": data.get("scanned_at"),
        })

        for skill in repo_skills:
            stamped = dict(skill)          # don't mutate the loaded structure
            stamped["repo"] = repo
            stamped["source"] = source
            stamped["repo_local"] = local
            skills.append(stamped)

    # Group by repo, then by skill name, so the default order reads naturally.
    skills.sort(key=lambda s: (s["repo"].lower(), s["name"].lower()))

    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregate": True,
        "repo": "all",                     # keeps render_html's title fallback happy
        "source": "all registered repos",
        "truncated": truncated_any,
        "repos": repos,
        "count": len(skills),
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate all repos/<repo>/skills.json into one combined HTML page."
    )
    parser.add_argument(
        "--repos-dir", default=DEFAULT_REPOS_DIR,
        help=f"Base directory holding per-repo scans (default: {DEFAULT_REPOS_DIR}).",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help=f"Output HTML file (default: <repos-dir>/{DEFAULT_ALL_VIEW}).",
    )
    parser.add_argument(
        "--include-unregistered", action="store_true",
        help="Include every skills.json on disk, not just registered repos.",
    )
    args = parser.parse_args(argv)

    data = aggregate(args.repos_dir, registered_only=not args.include_unregistered)
    if not data["repos"]:
        scope = "on disk" if args.include_unregistered else "registered"
        print(
            f"No {scope} repos with a {SKILLS_JSON} under {args.repos_dir}/. "
            f"Run register_repo.py first.",
            file=sys.stderr,
        )
        return 1

    output = args.output or (Path(args.repos_dir) / DEFAULT_ALL_VIEW)
    out_path = generate_skills_view.write_html(data, output)
    print(
        f"Aggregated {data['count']} skill(s) across {len(data['repos'])} repo(s) "
        f"-> {out_path}.",
        file=sys.stderr,
    )
    if data["truncated"]:
        print("Note: at least one repo's tree was truncated; some skills may be missing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
