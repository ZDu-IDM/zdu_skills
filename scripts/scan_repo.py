"""Scan a repo for Claude skills and save the result to ``repos/<repo>/skills.json``.

A "skill" is any directory containing a ``SKILL.md`` file. This script finds every
``SKILL.md`` in a repo — either a **GitHub repo** (via the GitHub API, no clone) or
an **already-cloned local directory** (walked on disk) — reads each skill's
``SKILL.md`` to parse its frontmatter (name, description, metadata), classifies it
as a **plugin** or **standalone** skill (via ``.claude-plugin/plugin.json`` and
``marketplace.json``), and records copy-pasteable **installation steps**. The result
is a single ``skills.json`` describing every skill. Only ``SKILL.md`` and the plugin
manifests are read; no supporting-file bytes are embedded.

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
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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

# Frontmatter keys that are intentionally *not* carried into skills.json (and so
# never reach the page): the title/description are promoted to their own fields,
# and license / allowed-tools / argument-hint are deliberately omitted.
_METADATA_EXCLUDE = {
    "name", "description", "license",
    "allowed-tools", "allowed_tools", "argument-hint", "argument_hint",
}


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
            if str(k).lower() not in _METADATA_EXCLUDE
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
# Path helpers
# ---------------------------------------------------------------------------

def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _skill_name_from_path(path: str) -> str:
    parent = _dirname(path)
    return parent.rsplit("/", 1)[-1] if parent else SKILL_FILE


# ---------------------------------------------------------------------------
# Local clone access  (filesystem mirror of the GitHub access layer above)
# ---------------------------------------------------------------------------

def is_local_repo(repo_input: str) -> bool:
    """True if ``repo_input`` points at an existing directory on disk.

    A GitHub reference like ``owner/repo`` is never a real directory, so an
    ``isdir`` check cleanly distinguishes the two input styles.
    """
    return bool(repo_input) and os.path.isdir(os.path.expanduser(repo_input))


def git_remote_slug(root: str) -> str | None:
    """Return the ``owner/repo`` slug of a local clone's ``origin`` remote, if any.

    Lets a locally-scanned clone still emit GitHub-style install steps
    (``git clone https://github.com/owner/repo.git``) instead of a machine-local
    path. Returns ``None`` when there's no git, no remote, or no GitHub origin.
    """
    try:
        url = subprocess.check_output(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group("slug") if match else None


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
# Plugin / marketplace detection + install steps
#   (ported from C:\work\claude_demo\detect-repo-skills\scan_skills.py)
# ---------------------------------------------------------------------------

PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"


def _plugin_roots_and_names(blobs: list[str], read_text) -> dict[str, str]:
    """Map each plugin *root* dir to its plugin name.

    A plugin root is the grandparent of a ``.claude-plugin/plugin.json`` file
    (manifest lives at ``<root>/.claude-plugin/plugin.json``). The name comes from
    the manifest's ``name`` field, falling back to the root dir's basename.
    """
    roots = {
        str(PurePosixPath(p).parent.parent)
        for p in blobs
        if p.endswith(PLUGIN_MANIFEST)
    }
    names: dict[str, str] = {}
    for root in roots:
        manifest = PLUGIN_MANIFEST if root == "." else f"{root}/{PLUGIN_MANIFEST}"
        name = None
        text = read_text(manifest)
        if text:
            try:
                name = json.loads(text).get("name")
            except json.JSONDecodeError:
                name = None
        names[root] = name or PurePosixPath(root).name
    return names


def _marketplace_name(blobs: list[str], read_text) -> str | None:
    """Return marketplace.json's top-level ``name`` (the install handle), or None.

    ``/plugin install <plugin>@<handle>`` resolves ``<handle>`` against the
    marketplace's own name, which is this top-level field.
    """
    if MARKETPLACE_MANIFEST not in set(blobs):
        return None
    text = read_text(MARKETPLACE_MANIFEST)
    if not text:
        return None
    try:
        return json.loads(text).get("name")
    except json.JSONDecodeError:
        return None


def _strip_dotslash(path: str) -> str:
    """Normalize a manifest path: drop leading ``./`` segments and surrounding slashes."""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.strip("/")


def _marketplace_plugin_skill_map(blobs: list[str], read_text) -> dict[str, str]:
    """Map each skill *directory* to its plugin name via marketplace.json's ``plugins``.

    Some repos (notably anthropics/skills) declare plugins *inline* in
    ``marketplace.json`` — each plugin lists its member skills by path in a
    ``skills`` array (e.g. ``"./skills/xlsx"``) — instead of dropping a
    ``plugin.json`` inside each plugin root. Those skills have no ``plugin.json``
    ancestor, so :func:`_find_owning_plugin` can't see them. This resolves the
    by-reference style: ``{<skill_dir>: <plugin name>}``, where ``<skill_dir>`` is
    the plugin ``source`` joined with each ``skills`` entry (both ``./``-relative).
    """
    if MARKETPLACE_MANIFEST not in set(blobs):
        return {}
    text = read_text(MARKETPLACE_MANIFEST)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    mapping: dict[str, str] = {}
    for plugin in data.get("plugins", []) or []:
        if not isinstance(plugin, dict):
            continue
        name = plugin.get("name")
        if not name:
            continue
        source = _strip_dotslash(plugin.get("source") or "")
        for skill_ref in plugin.get("skills", []) or []:
            ref = _strip_dotslash(skill_ref if isinstance(skill_ref, str) else "")
            skill_dir = "/".join(p for p in (source, ref) if p)
            if skill_dir:
                mapping[skill_dir] = name
    return mapping


def _find_owning_plugin(skill_dir: str, plugin_roots) -> str | None:
    """Return the nearest ancestor (or self) that is a plugin root, else None."""
    current = PurePosixPath(skill_dir)
    while True:
        text = str(current)
        if text in plugin_roots:
            return text
        if text in (".", ""):
            return None
        current = current.parent


def _install_steps(
    skill_name: str,
    skill_dir: str,
    plugin_name: str | None,
    *,
    market_source: str,
    market_name: str | None,
    clone_url: str,
    local_root: str | None,
) -> list[str]:
    """Return the copy-pasteable install steps for one skill.

    Plugin skills install via the ``/plugin`` marketplace flow; standalone skills
    are copied into ``~/.claude/skills`` (from a clone, or directly from disk when
    the repo was scanned locally).
    """
    if plugin_name is not None:
        return [
            f"/plugin marketplace add {market_source}",
            f"/plugin install {plugin_name}@{market_name or plugin_name}",
            f"# ships with plugin '{plugin_name}'; invoke as /{plugin_name}:{skill_name}",
        ]

    src_dir = skill_dir or "."
    if local_root:
        # Files are already on disk; copy straight from the local repo root.
        on_disk = (Path(local_root) / src_dir).as_posix()
        return [
            f"mkdir -p ~/.claude/skills/{skill_name}                 # personal scope (all projects)",
            f'cp -r "{on_disk}" ~/.claude/skills/{skill_name}',
            f'# OR project scope: cp -r "{on_disk}" <project>/.claude/skills/{skill_name}',
            f"# then invoke as /{skill_name}",
        ]
    return [
        f"git clone {clone_url} /tmp/repo",
        f"mkdir -p ~/.claude/skills/{skill_name}                 # personal scope (all projects)",
        f"cp -r /tmp/repo/{src_dir} ~/.claude/skills/{skill_name}",
        f"# OR project scope: cp -r /tmp/repo/{src_dir} <project>/.claude/skills/{skill_name}",
        f"# then invoke as /{skill_name}",
    ]


# ---------------------------------------------------------------------------
# Scan: build the skill records from each SKILL.md's frontmatter
# ---------------------------------------------------------------------------

def _build_skills(
    blobs: list[str],
    read_text,           # (rel_path) -> str | None  (decoded file contents)
    make_url,            # (rel_path) -> str
    install_ctx: dict,   # market_source / clone_url / local_root for install steps
) -> list[dict]:
    """Turn a flat file list into skill records from each ``SKILL.md``.

    Backend-agnostic: ``read_text`` and ``make_url`` abstract over "fetch from
    GitHub" vs. "read from disk", so the GitHub and local paths share this loop.
    Only ``SKILL.md`` (and the plugin/marketplace manifests) are read — no
    supporting files are fetched. Each skill is classified as ``plugin`` or
    ``standalone`` and stamped with copy-pasteable installation steps.
    """
    skill_md_paths = sorted(p for p in blobs if p.rsplit("/", 1)[-1] == SKILL_FILE)
    plugin_names = _plugin_roots_and_names(blobs, read_text)
    plugin_roots = set(plugin_names)
    market_name = _marketplace_name(blobs, read_text)
    market_skill_map = _marketplace_plugin_skill_map(blobs, read_text)

    skills = []
    for md_path in skill_md_paths:
        skill_dir = _dirname(md_path)
        skill_md_text = read_text(md_path) or ""
        fm_name, description, metadata = parse_skill_md(skill_md_text)
        name = fm_name or _skill_name_from_path(md_path)

        # Prefer a plugin.json-based owning root; fall back to marketplace.json's
        # by-reference skill list for repos that declare plugins inline.
        owning_root = _find_owning_plugin(skill_dir, plugin_roots)
        plugin_name = plugin_names.get(owning_root) if owning_root else None
        if plugin_name is None:
            plugin_name = market_skill_map.get(skill_dir)
        installation = _install_steps(
            name, skill_dir, plugin_name,
            market_source=install_ctx["market_source"],
            market_name=market_name,
            clone_url=install_ctx["clone_url"],
            local_root=install_ctx.get("local_root"),
        )

        skills.append({
            "name": name,
            "description": description,
            "path": md_path,
            "url": make_url(md_path),
            "type": "plugin" if plugin_name else "standalone",
            "plugin": plugin_name,        # None for standalone skills
            "installation": installation,
            "metadata": _jsonsafe(metadata),
        })

    skills.sort(key=lambda s: s["name"].lower())
    return skills


def scan_repo(repo_input: str) -> dict:
    """Scan a GitHub repo *or* a local clone, depending on the input.

    If ``repo_input`` is an existing directory it is walked on disk; otherwise it
    is treated as a GitHub reference (``owner/repo`` or a URL). The returned dict
    is the full ``skills.json`` payload (schema_version + timestamp + skills).
    """
    if is_local_repo(repo_input):
        data = scan_local_repo(repo_input)
    else:
        data = scan_github_repo(repo_input)

    # Stamp schema/version metadata at the front so skills.json self-describes.
    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **data,
    }


def scan_github_repo(repo_input: str) -> dict:
    """Detect every skill via the GitHub API, reading each SKILL.md's frontmatter."""
    owner, repo, requested_ref = parse_repo_input(repo_input)
    ref = resolve_ref(owner, repo, requested_ref)
    blobs, truncated = list_tree(owner, repo, ref)

    def read_text(path: str) -> str | None:
        content = fetch_raw_bytes(owner, repo, ref, path)
        return content.decode("utf-8", errors="replace") if content is not None else None

    def make_url(path: str) -> str:
        return f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"

    install_ctx = {
        "market_source": f"{owner}/{repo}",
        "clone_url": f"https://github.com/{owner}/{repo}.git",
        "local_root": None,
    }
    skills = _build_skills(blobs, read_text, make_url, install_ctx)
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


def scan_local_repo(repo_input: str) -> dict:
    """Detect every skill by walking a local clone, reading each SKILL.md's frontmatter."""
    root = os.path.abspath(os.path.expanduser(repo_input))
    if not os.path.isdir(root):
        raise ScanError(f"Local path is not a directory: {root}")

    repo = os.path.basename(root.rstrip(os.sep)) or root
    blobs = list_local_tree(root)

    def read_text(path: str) -> str | None:
        try:
            with open(os.path.join(root, path), "rb") as fh:
                return fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None

    def make_url(path: str) -> str:
        # A file:// URI so the link opens the actual file from the browser.
        return (Path(root) / path).as_uri()

    # Prefer GitHub-style install steps when the clone has a github.com origin;
    # otherwise fall back to copying straight from the local path on disk.
    slug = git_remote_slug(root)
    install_ctx = {
        "market_source": slug or root,
        "clone_url": f"https://github.com/{slug}.git" if slug else root,
        "local_root": root,
    }
    skills = _build_skills(blobs, read_text, make_url, install_ctx)
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
    args = parser.parse_args(argv)

    try:
        print(f"Scanning {args.repo} …", file=sys.stderr)
        data = scan_repo(args.repo)
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
