"""Scan a repo for Claude skills and save the result to ``repos/<repo>/skills.json``.

A "skill" is any directory containing a ``SKILL.md`` file. This script finds every
``SKILL.md`` in a repo — either a **GitHub repo** (via the GitHub API, no clone) or
an **already-cloned local directory** (walked on disk) — reads each skill's files,
and writes a single self-contained ``skills.json`` that carries *everything* needed
to later build the interactive ``repo_skills.html`` (including each file's bytes,
base64-encoded, for the in-browser ZIP download).

This is the *scan* half of the original ``scan_repo_skills.py``; rendering lives in
``generate_skills_view.py``. The orchestrator ``register_repo.py`` calls both.

Output layout::

    repos/<repo-name>/skills.json

where ``<repo-name>`` is the bare repo name (GitHub ``owner/repo`` -> ``repo``).

Usage:
    python scan_repo.py --repo anthropics/skills
    python scan_repo.py --repo https://github.com/owner/repo --repos-dir repos
    python scan_repo.py --repo C:\\work\\AI\\starsim_ai      # local clone

Set GITHUB_TOKEN to raise the API rate limit (60 -> 5000 req/hr) and scan private repos.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    import yaml  # PyYAML — parses SKILL.md frontmatter, including lists/dicts.
except ImportError:  # pragma: no cover - script still runs; list values degrade.
    yaml = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bump when the skills.json shape changes so the (future) aggregator can adapt.
SCHEMA_VERSION = 1

SKILL_FILE = "SKILL.md"
SKILLS_JSON = "skills.json"
DEFAULT_REPOS_DIR = "repos"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
API_TIMEOUT = 15
RAW_TIMEOUT = 20
# Guard against accidentally bloating the JSON/HTML with huge binary blobs. Files
# larger than this are still *listed*, but their bytes aren't embedded, so they
# are skipped from the in-browser ZIP. Tune via --max-file-bytes.
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# SKILL.md frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL)
_FRONTMATTER_KV_RE = re.compile(
    r"^(?P<key>[A-Za-z_][\w-]*)\s*:\s*(?P<value>.*?)\s*$", re.MULTILINE
)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter_block(block: str) -> dict:
    """Parse a YAML frontmatter block into a dict (PyYAML, regex fallback)."""
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            return data

    out: dict = {}
    for kv in _FRONTMATTER_KV_RE.finditer(block):
        out[kv.group("key")] = _strip_quotes(kv.group("value"))
    return out


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _jsonsafe(value):
    """Coerce a parsed-YAML value into a JSON-serializable one."""
    if isinstance(value, dict):
        return {str(k): _jsonsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def parse_skill_md(text: str) -> tuple[str | None, str, dict]:
    """Return ``(name, description, metadata)`` for SKILL.md contents."""
    name: str | None = None
    description = ""
    metadata: dict = {}
    body = text

    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        fm_block = fm_match.group("body")
        body = text[fm_match.end():]
        data = _parse_frontmatter_block(fm_block)
        lowered = {str(k).lower(): v for k, v in data.items()}
        name = _as_text(lowered.get("name")) or None
        description = _as_text(lowered.get("description"))
        metadata = {
            k: v
            for k, v in data.items()
            if str(k).lower() not in ("name", "description")
        }

    if not description:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped
                break

    return name, description, metadata


# ---------------------------------------------------------------------------
# Repo input parsing
# ---------------------------------------------------------------------------

class ScanError(Exception):
    """User-facing error."""


def parse_repo_input(text: str) -> tuple[str, str, str | None]:
    """Parse a repo reference into ``(owner, repo, ref_or_None)``."""
    text = (text or "").strip()
    if not text:
        raise ScanError("Please provide a GitHub repository URL or owner/repo.")

    if "github.com" in text:
        if not re.match(r"^https?://", text):
            text = "https://" + text
        path = urlparse(text).path
    else:
        path = text

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ScanError(
            "Could not read 'owner/repo' from that input. "
            "Try e.g. anthropics/skills or https://github.com/anthropics/skills."
        )

    owner, repo = parts[0], parts[1]
    repo = repo[:-4] if repo.endswith(".git") else repo

    ref: str | None = None
    if len(parts) >= 4 and parts[2] == "tree":
        ref = "/".join(parts[3:])

    return owner, repo, ref


# ---------------------------------------------------------------------------
# GitHub access
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_get_json(url: str) -> dict:
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=API_TIMEOUT)
    except requests.RequestException as exc:
        raise ScanError(f"Network error contacting GitHub: {exc}")

    if resp.status_code == 404:
        raise ScanError("Repository or branch not found.")
    if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
        raise ScanError(
            "GitHub API rate limit exceeded. Set a GITHUB_TOKEN environment "
            "variable to raise the limit, then try again."
        )
    if not resp.ok:
        raise ScanError(f"GitHub API error ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def resolve_ref(owner: str, repo: str, ref: str | None) -> str:
    if ref:
        return ref
    data = _github_get_json(f"{GITHUB_API}/repos/{owner}/{repo}")
    branch = data.get("default_branch")
    if not branch:
        raise ScanError("Could not determine the repository's default branch.")
    return branch


def list_tree(owner: str, repo: str, ref: str) -> tuple[list[str], bool]:
    """Return ``(all_blob_paths, truncated)`` by listing the repo tree once."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    data = _github_get_json(url)
    truncated = bool(data.get("truncated"))
    blobs = [
        item["path"]
        for item in data.get("tree", [])
        if item.get("type") == "blob" and item.get("path")
    ]
    return blobs, truncated


def fetch_raw_bytes(owner: str, repo: str, ref: str, path: str) -> bytes | None:
    """Fetch one file's raw bytes from raw.githubusercontent.com (no rate limit)."""
    url = f"{GITHUB_RAW}/{owner}/{repo}/{ref}/{path}"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=RAW_TIMEOUT)
        if resp.ok:
            return resp.content
    except requests.RequestException:
        pass
    return None


# ---------------------------------------------------------------------------
# Supporting-file attribution
# ---------------------------------------------------------------------------

def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _skill_name_from_path(path: str) -> str:
    parent = _dirname(path)
    return parent.rsplit("/", 1)[-1] if parent else SKILL_FILE


def _attribute_supporting_files(
    blobs: list[str], skill_md_paths: list[str]
) -> dict[str, list[str]]:
    """Map each skill's SKILL.md path to its supporting files (nearest folder wins)."""
    skill_set = set(skill_md_paths)
    dir_to_md = {_dirname(md): md for md in skill_md_paths}
    dirs = sorted((d for d in dir_to_md if d), key=len, reverse=True)

    result: dict[str, list[str]] = {md: [] for md in skill_md_paths}
    for blob in blobs:
        if blob in skill_set:
            continue
        for d in dirs:
            if blob.startswith(d + "/"):
                result[dir_to_md[d]].append(blob)
                break
    for md in result:
        result[md].sort()
    return result


# ---------------------------------------------------------------------------
# Local clone access  (filesystem mirror of the GitHub access layer above)
# ---------------------------------------------------------------------------

def is_local_repo(repo_input: str) -> bool:
    """True if ``repo_input`` points at an existing directory on disk.

    A GitHub reference like ``owner/repo`` is never a real directory, so an
    ``isdir`` check cleanly distinguishes the two input styles.
    """
    return bool(repo_input) and os.path.isdir(os.path.expanduser(repo_input))


def list_local_tree(root: str) -> list[str]:
    """Return every file under ``root`` as a repo-relative, forward-slash path."""
    blobs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune VCS/noise dirs in place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            blobs.append(rel)
    return blobs


# ---------------------------------------------------------------------------
# Scan: build the full dataset (including file bytes for client-side ZIP)
# ---------------------------------------------------------------------------

def _build_skills(
    blobs: list[str],
    default_folder: str,
    read_bytes,          # (rel_path) -> bytes | None
    make_url,            # (rel_path) -> str
    max_file_bytes: int,
) -> list[dict]:
    """Turn a flat file list into skill records, embedding bytes for the ZIP.

    Backend-agnostic: ``read_bytes`` and ``make_url`` abstract over "fetch from
    GitHub" vs. "read from disk", so the GitHub and local paths share this loop.
    """
    skill_md_paths = sorted(p for p in blobs if p.rsplit("/", 1)[-1] == SKILL_FILE)
    supporting = _attribute_supporting_files(blobs, skill_md_paths)

    skills = []
    for md_path in skill_md_paths:
        skill_dir = _dirname(md_path)
        # The folder name used as the ZIP's top-level directory.
        folder = skill_dir.rsplit("/", 1)[-1] if skill_dir else default_folder

        member_paths = [md_path] + supporting.get(md_path, [])
        files = []
        skill_md_text = ""
        for repo_path in member_paths:
            rel = repo_path[len(skill_dir) + 1:] if skill_dir else repo_path
            content = read_bytes(repo_path)
            entry = {
                "path": rel,            # path relative to the skill folder
                "url": make_url(repo_path),
                "size": len(content) if content is not None else 0,
            }
            if content is None:
                entry["b64"] = None     # unreadable — listed but not zippable
            elif len(content) > max_file_bytes:
                entry["b64"] = None     # too big to embed — listed but not zippable
                entry["skipped"] = True
            else:
                entry["b64"] = base64.b64encode(content).decode("ascii")
            files.append(entry)
            if repo_path == md_path and content is not None:
                skill_md_text = content.decode("utf-8", errors="replace")

        fm_name, description, metadata = parse_skill_md(skill_md_text)
        skills.append({
            "name": fm_name or _skill_name_from_path(md_path),
            "folder": folder,
            "description": description,
            "path": md_path,
            "url": make_url(md_path),
            "metadata": _jsonsafe(metadata),
            "files": files,
        })

    skills.sort(key=lambda s: s["name"].lower())
    return skills


def scan_repo(repo_input: str, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> dict:
    """Scan a GitHub repo *or* a local clone, depending on the input.

    If ``repo_input`` is an existing directory it is walked on disk; otherwise it
    is treated as a GitHub reference (``owner/repo`` or a URL). The returned dict
    is the full ``skills.json`` payload (schema_version + timestamp + skills).
    """
    if is_local_repo(repo_input):
        data = scan_local_repo(repo_input, max_file_bytes)
    else:
        data = scan_github_repo(repo_input, max_file_bytes)

    # Stamp schema/version metadata at the front so skills.json self-describes.
    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **data,
    }


def scan_github_repo(repo_input: str, max_file_bytes: int) -> dict:
    """Detect every skill via the GitHub API, embedding file bytes (base64)."""
    owner, repo, requested_ref = parse_repo_input(repo_input)
    ref = resolve_ref(owner, repo, requested_ref)
    blobs, truncated = list_tree(owner, repo, ref)

    def read_bytes(path: str) -> bytes | None:
        return fetch_raw_bytes(owner, repo, ref, path)

    def make_url(path: str) -> str:
        return f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"

    skills = _build_skills(blobs, repo, read_bytes, make_url, max_file_bytes)
    return {
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "local": False,
        "source": f"{owner}/{repo}",
        "truncated": truncated,
        "count": len(skills),
        "skills": skills,
    }


def scan_local_repo(repo_input: str, max_file_bytes: int) -> dict:
    """Detect every skill by walking a local clone, embedding file bytes (base64)."""
    root = os.path.abspath(os.path.expanduser(repo_input))
    if not os.path.isdir(root):
        raise ScanError(f"Local path is not a directory: {root}")

    repo = os.path.basename(root.rstrip(os.sep)) or root
    blobs = list_local_tree(root)

    def read_bytes(path: str) -> bytes | None:
        try:
            with open(os.path.join(root, path), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def make_url(path: str) -> str:
        # A file:// URI so the link opens the actual file from the browser.
        return (Path(root) / path).as_uri()

    skills = _build_skills(blobs, repo, read_bytes, make_url, max_file_bytes)
    return {
        "owner": "",
        "repo": repo,
        "ref": "local clone",
        "local": True,
        "source": root,
        "truncated": False,
        "count": len(skills),
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# Persisting the scan result
# ---------------------------------------------------------------------------

def _safe_repo_name(repo: str) -> str:
    """Make a repo name safe to use as a single directory component."""
    name = (repo or "").strip().strip("/").replace("/", "_").replace(os.sep, "_")
    return name or "repo"


def skills_json_path(repo: str, repos_dir: str = DEFAULT_REPOS_DIR) -> Path:
    """Return ``<repos_dir>/<repo-name>/skills.json`` (bare repo name)."""
    return Path(repos_dir) / _safe_repo_name(repo) / SKILLS_JSON


def write_skills_json(data: dict, repos_dir: str = DEFAULT_REPOS_DIR) -> Path:
    """Write ``data`` to ``<repos_dir>/<repo-name>/skills.json`` and return its path."""
    out_path = skills_json_path(data["repo"], repos_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a GitHub repo or local clone for Claude skills; write repos/<repo>/skills.json."
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
    args = parser.parse_args(argv)

    try:
        print(f"Scanning {args.repo} …", file=sys.stderr)
        data = scan_repo(args.repo, args.max_file_bytes)
        out_path = write_skills_json(data, args.repos_dir)
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Found {data['count']} skill(s) in {data.get('source') or data['repo']} "
        f"(ref: {data['ref']}). Wrote {out_path}.",
        file=sys.stderr,
    )
    if data["truncated"]:
        print("Note: GitHub truncated the file tree; some skills may be missing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
