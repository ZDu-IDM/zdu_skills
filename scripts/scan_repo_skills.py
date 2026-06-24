"""Scan a repo for Claude skills and emit a single, self-contained HTML page.

A "skill" is any directory containing a ``SKILL.md`` file. This script finds every
``SKILL.md`` in a repo — either a **GitHub repo** (via the GitHub API, no clone) or
an **already-cloned local directory** (walked on disk) — reads each skill's files,
and writes one static ``repo_skills.html`` that lists every skill and lets you:

    * search / filter the skills live (client-side),
    * export the scan result as JSON or CSV, and
    * download any skill's folder as a ZIP — built entirely in the browser from
      file bytes embedded in the page, so the page needs no server and works offline.

The scanning core (repo-input parsing, GitHub access, SKILL.md frontmatter
parsing, supporting-file attribution) is ported from the companion Flask app at
``C:\\work\\claude_demo\\repo-skills`` (app.py). The difference is the *output*:
instead of serving an interactive site, this produces a single file you can email,
commit, or open by double-clicking.

Usage:
    python scan_repo_skills.py --repo anthropics/skills
    python scan_repo_skills.py --repo https://github.com/owner/repo --output skills.html
    python scan_repo_skills.py --repo C:\\work\\AI\\starsim_ai -o skills.html  # local clone

Set GITHUB_TOKEN to raise the API rate limit (60 -> 5000 req/hr) and scan private repos.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
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

SKILL_FILE = "SKILL.md"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
API_TIMEOUT = 15
RAW_TIMEOUT = 20
# Guard against accidentally bloating the HTML with huge binary blobs. Files
# larger than this are still *listed*, but their bytes aren't embedded, so they
# are skipped from the in-browser ZIP. Tune via --max-file-bytes.
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# SKILL.md frontmatter parsing  (ported from repo-skills/app.py)
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
# Repo input parsing  (ported from repo-skills/app.py)
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
# GitHub access  (ported from repo-skills/app.py)
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
# Supporting-file attribution  (ported from repo-skills/app.py)
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


def scan_repo(repo_input: str, max_file_bytes: int) -> dict:
    """Scan a GitHub repo *or* a local clone, depending on the input.

    If ``repo_input`` is an existing directory it is walked on disk; otherwise it
    is treated as a GitHub reference (``owner/repo`` or a URL).
    """
    if is_local_repo(repo_input):
        return scan_local_repo(repo_input, max_file_bytes)
    return scan_github_repo(repo_input, max_file_bytes)


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
# HTML generation
# ---------------------------------------------------------------------------

def render_html(data: dict) -> str:
    """Render the single self-contained HTML page from a scan result dict."""
    # Embed the dataset as JSON in a <script type="application/json"> block.
    # Escaping "</" -> "<\\/" prevents a stray "</script>" inside string data
    # from prematurely closing the script element.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"Skills in {data.get('source') or data['repo']}")
    return _HTML_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)


# The page is intentionally one file with no external dependencies: styles are
# inline, and the ZIP encoder below is a minimal pure-JS STORE-method writer
# (with a CRC32 table) so downloads work offline with no CDN.
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0d1117; --card:#161b22; --border:#30363d; --fg:#e6edf3; --muted:#8b949e; --accent:#2f81f7; --accent2:#238636; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:24px 20px 12px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--bg); z-index:5; }
  h1 { margin:0 0 4px; font-size:20px; }
  h1 a { color:var(--accent); text-decoration:none; }
  .meta { color:var(--muted); font-size:13px; }
  .warn { color:#d29922; font-size:13px; margin-top:6px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:14px; }
  input[type=search] { flex:1 1 280px; min-width:200px; padding:9px 12px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:14px; }
  button { padding:8px 13px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:13px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent2); border-color:var(--accent2); color:#fff; }
  button:disabled { opacity:0.4; cursor:default; border-color:var(--border); }
  select { padding:8px 10px; border-radius:8px; border:1px solid var(--border); background:var(--card); color:var(--fg); font-size:13px; cursor:pointer; }
  label.ctrl { color:var(--muted); font-size:12px; display:flex; align-items:center; gap:5px; }
  .count { color:var(--muted); font-size:13px; margin-left:auto; }
  .pager { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:center; margin-top:18px; color:var(--muted); font-size:13px; }
  main { padding:18px 20px 60px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px; }
  .card h2 { margin:0; font-size:16px; }
  .card .desc { color:var(--fg); font-size:13px; line-height:1.45; }
  .card .path { color:var(--muted); font-size:12px; word-break:break-all; }
  .card .path a { color:var(--accent); text-decoration:none; }
  .tags { display:flex; flex-wrap:wrap; gap:5px; }
  .tag { font-size:11px; background:#21262d; border:1px solid var(--border); border-radius:999px; padding:2px 8px; color:var(--muted); }
  details { font-size:12px; color:var(--muted); }
  details summary { cursor:pointer; }
  details ul { margin:6px 0 0; padding-left:18px; }
  .row { display:flex; gap:8px; margin-top:auto; padding-top:6px; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
  a.filelink { color:var(--accent); text-decoration:none; }
  .skip { color:#d29922; }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="meta" id="subtitle"></div>
  <div class="warn" id="truncated" style="display:none"></div>
  <div class="toolbar">
    <input type="search" id="filter" placeholder="Filter by name, description, path, metadata, or file…" autofocus>
    <label class="ctrl">Sort
      <select id="sort">
        <option value="name-asc">Name (A→Z)</option>
        <option value="name-desc">Name (Z→A)</option>
        <option value="path-asc">Path (A→Z)</option>
        <option value="files-desc">Files (most)</option>
        <option value="files-asc">Files (fewest)</option>
      </select>
    </label>
    <label class="ctrl">Per page
      <select id="perPage">
        <option value="6">6</option>
        <option value="12" selected>12</option>
        <option value="24">24</option>
        <option value="48">48</option>
        <option value="all">All</option>
      </select>
    </label>
    <button id="exportJson">Export JSON</button>
    <button id="exportCsv">Export CSV</button>
    <span class="count" id="count"></span>
  </div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">No skills match your filter.</div>
  <div class="pager" id="pager" style="display:none">
    <button id="prev">‹ Prev</button>
    <span id="pageInfo"></span>
    <button id="next">Next ›</button>
  </div>
</main>

<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);

/* ---- tiny pure-JS ZIP (STORE method) so downloads work offline ---------- */
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function strBytes(s) { return new TextEncoder().encode(s); }
// Build a ZIP archive (no compression) from [{name, bytes}].
function makeZip(entries) {
  const chunks = [], central = [];
  let offset = 0;
  const u16 = n => [n & 0xFF, (n >>> 8) & 0xFF];
  const u32 = n => [n & 0xFF, (n >>> 8) & 0xFF, (n >>> 16) & 0xFF, (n >>> 24) & 0xFF];
  for (const e of entries) {
    const nameBytes = strBytes(e.name);
    const crc = crc32(e.bytes), size = e.bytes.length;
    const local = [].concat(
      u32(0x04034b50), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(size), u32(size), u16(nameBytes.length), u16(0)
    );
    chunks.push(new Uint8Array(local), nameBytes, e.bytes);
    const localLen = local.length + nameBytes.length + size;
    central.push([].concat(
      u32(0x02014b50), u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(size), u32(size), u16(nameBytes.length),
      u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset)
    ), Array.from(nameBytes));
    offset += localLen;
  }
  const centralStart = offset;
  let centralLen = 0;
  const centralChunks = [];
  for (const c of central) { const a = new Uint8Array(c); centralChunks.push(a); centralLen += a.length; }
  const end = [].concat(
    u32(0x06054b50), u16(0), u16(0), u16(entries.length), u16(entries.length),
    u32(centralLen), u32(centralStart), u16(0)
  );
  const parts = [...chunks, ...centralChunks, new Uint8Array(end)];
  return new Blob(parts, { type: "application/zip" });
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadSkillZip(skill) {
  const entries = [];
  for (const f of skill.files) {
    if (!f.b64) continue;  // unreadable / too-large files are skipped
    entries.push({ name: skill.folder + "/" + f.path, bytes: b64ToBytes(f.b64) });
  }
  if (!entries.length) { alert("No embedded files available to zip for this skill."); return; }
  download(makeZip(entries), (skill.folder || "skill").replace(/[^\w.-]+/g, "_") + ".zip");
}

/* ---- export helpers ----------------------------------------------------- */
// Filename stem: "owner_repo" for GitHub, just "repo" for a local clone.
const EXPORT_STEM = (DATA.owner ? DATA.owner + "_" : "") + DATA.repo;
function exportJson() {
  download(new Blob([JSON.stringify(DATA, null, 2)], { type: "application/json" }),
           EXPORT_STEM + "_skills.json");
}
function csvCell(v) { return '"' + String(v).replace(/"/g, '""') + '"'; }
function exportCsv() {
  const rows = [["name", "description", "path", "url", "metadata", "files"].join(",")];
  for (const s of DATA.skills) {
    rows.push([
      csvCell(s.name), csvCell(s.description), csvCell(s.path), csvCell(s.url),
      csvCell(JSON.stringify(s.metadata || {})),
      csvCell(s.files.map(f => f.path).join(" | ")),
    ].join(","));
  }
  // UTF-8 BOM so Excel reads non-ASCII correctly.
  download(new Blob(["﻿" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" }),
           EXPORT_STEM + "_skills.csv");
}

/* ---- rendering ---------------------------------------------------------- */
const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Flatten a skill to one lowercase string for substring filtering.
function haystack(s) {
  const parts = [s.name, s.description, s.path, JSON.stringify(s.metadata || {})];
  for (const f of s.files) parts.push(f.path);
  return parts.join("  ").toLowerCase();
}
DATA.skills.forEach(s => { s._hay = haystack(s); });

function metaTags(md) {
  if (!md || typeof md !== "object") return "";
  const tags = [];
  for (const [k, v] of Object.entries(md)) {
    const val = Array.isArray(v) ? v.join(", ") : (typeof v === "object" ? JSON.stringify(v) : v);
    tags.push('<span class="tag">' + esc(k) + ": " + esc(val) + "</span>");
  }
  return tags.length ? '<div class="tags">' + tags.join("") + "</div>" : "";
}

function fileList(s) {
  if (!s.files.length) return "";
  const items = s.files.map(f => {
    const skip = f.b64 ? "" : ' <span class="skip">(not embedded)</span>';
    return "<li><a class='filelink' href='" + esc(f.url) + "' target='_blank' rel='noopener'>" +
           esc(f.path) + "</a>" + skip + "</li>";
  }).join("");
  return "<details><summary>" + s.files.length + " file(s)</summary><ul>" + items + "</ul></details>";
}

function card(s, i) {
  return '<div class="card">' +
    "<h2>" + esc(s.name) + "</h2>" +
    (s.description ? '<div class="desc">' + esc(s.description) + "</div>" : "") +
    '<div class="path"><a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.path) + "</a></div>" +
    metaTags(s.metadata) +
    fileList(s) +
    '<div class="row"><button class="primary" data-zip="' + i + '">Download ZIP</button></div>' +
  "</div>";
}

const grid = document.getElementById("grid");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("count");
const filterEl = document.getElementById("filter");
const sortEl = document.getElementById("sort");
const perPageEl = document.getElementById("perPage");
const pagerEl = document.getElementById("pager");
const pageInfoEl = document.getElementById("pageInfo");
const prevEl = document.getElementById("prev");
const nextEl = document.getElementById("next");

let page = 1;  // 1-based; reset to 1 whenever the matched set changes.

// Comparators keyed by the Sort dropdown's value. Each pair is {s, i} where
// i is the original index into DATA.skills (preserved so Download ZIP works).
const SORTERS = {
  "name-asc":   (a, b) => a.s.name.toLowerCase().localeCompare(b.s.name.toLowerCase()),
  "name-desc":  (a, b) => b.s.name.toLowerCase().localeCompare(a.s.name.toLowerCase()),
  "path-asc":   (a, b) => a.s.path.toLowerCase().localeCompare(b.s.path.toLowerCase()),
  "files-desc": (a, b) => b.s.files.length - a.s.files.length || SORTERS["name-asc"](a, b),
  "files-asc":  (a, b) => a.s.files.length - b.s.files.length || SORTERS["name-asc"](a, b),
};

function matched() {
  const q = filterEl.value.trim().toLowerCase();
  const terms = q ? q.split(/\s+/) : [];
  const list = DATA.skills
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => terms.every(t => s._hay.includes(t)));
  list.sort(SORTERS[sortEl.value] || SORTERS["name-asc"]);
  return list;
}

function render() {
  const list = matched();
  const per = perPageEl.value === "all" ? list.length || 1 : parseInt(perPageEl.value, 10);
  const pages = Math.max(1, Math.ceil(list.length / per));
  if (page > pages) page = pages;          // clamp after filtering shrinks the set
  const start = (page - 1) * per;
  const slice = list.slice(start, start + per);

  grid.innerHTML = slice.map(({ s, i }) => card(s, i)).join("");
  emptyEl.style.display = list.length ? "none" : "block";
  countEl.textContent = list.length + " of " + DATA.skills.length + " skill(s)";

  // Pagination bar only appears when the matched set exceeds one page.
  if (list.length > per) {
    pagerEl.style.display = "flex";
    pageInfoEl.textContent = "Page " + page + " of " + pages +
      "  ·  showing " + (start + 1) + "–" + (start + slice.length);
    prevEl.disabled = page <= 1;
    nextEl.disabled = page >= pages;
  } else {
    pagerEl.style.display = "none";
  }
}

// Changing the filter, sort, or page size returns the user to page 1.
function resetAndRender() { page = 1; render(); }

/* ---- wire up ------------------------------------------------------------ */
if (DATA.local) {
  // Local clone: no GitHub URL to link to, so show the source path as plain text.
  document.getElementById("title").innerHTML = "Skills in " + esc(DATA.repo);
} else {
  document.getElementById("title").innerHTML =
    'Skills in <a href="https://github.com/' + esc(DATA.owner) + "/" + esc(DATA.repo) +
    '" target="_blank" rel="noopener">' + esc(DATA.owner) + "/" + esc(DATA.repo) + "</a>";
}
document.getElementById("subtitle").textContent =
  DATA.count + " skill(s) · " + (DATA.local ? "local clone: " + (DATA.source || DATA.repo)
                                            : "branch/ref: " + DATA.ref);
if (DATA.truncated) {
  const w = document.getElementById("truncated");
  w.style.display = "block";
  w.textContent = "⚠ The repo tree was truncated by GitHub (very large repo) — some skills may be missing.";
}
filterEl.addEventListener("input", resetAndRender);
sortEl.addEventListener("change", resetAndRender);
perPageEl.addEventListener("change", resetAndRender);
prevEl.addEventListener("click", () => { if (page > 1) { page--; render(); window.scrollTo(0, 0); } });
nextEl.addEventListener("click", () => { page++; render(); window.scrollTo(0, 0); });
document.getElementById("exportJson").addEventListener("click", exportJson);
document.getElementById("exportCsv").addEventListener("click", exportCsv);
grid.addEventListener("click", e => {
  const btn = e.target.closest("button[data-zip]");
  if (btn) downloadSkillZip(DATA.skills[Number(btn.dataset.zip)]);
});
render();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a GitHub repo or local clone for Claude skills and write a self-contained HTML page."
    )
    parser.add_argument(
        "--repo", required=True,
        help=(
            "GitHub repo ('owner/repo', a URL, or a '/tree/<ref>' URL to pin a "
            "branch/tag) OR a path to an already-cloned local directory."
        ),
    )
    parser.add_argument(
        "--output", "-o", default="repo_skills.html",
        help="Output HTML file (default: repo_skills.html).",
    )
    parser.add_argument(
        "--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES,
        help="Skip embedding files larger than this (still listed). Default: 5 MiB.",
    )
    args = parser.parse_args(argv)

    try:
        print(f"Scanning {args.repo} …", file=sys.stderr)
        data = scan_repo(args.repo, args.max_file_bytes)
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    page = render_html(data)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    print(
        f"Found {data['count']} skill(s) in {data.get('source') or data['repo']} "
        f"(ref: {data['ref']}). Wrote {args.output}.",
        file=sys.stderr,
    )
    if data["truncated"]:
        print("Note: GitHub truncated the file tree; some skills may be missing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
