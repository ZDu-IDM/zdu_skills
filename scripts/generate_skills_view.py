"""Render a self-contained ``repo_skills.html`` from a ``skills.json`` scan result.

This is the *rendering* half of the original ``scan_repo_skills.py``: it does no
scanning and never touches the network. It takes the ``skills.json`` produced by
``scan_repo.py`` and emits one static HTML page that lets you search/filter the
skills, export JSON/CSV, and download any skill's folder as a ZIP — all built in
the browser from file bytes embedded in the page, so it works offline.

The page is intentionally one file with no external dependencies: styles are
inline, and the ZIP encoder is a minimal pure-JS STORE-method writer (with a
CRC32 table) so downloads work offline with no CDN.

Usage:
    python generate_skills_view.py --input repos/skills/skills.json
    python generate_skills_view.py --input repos/skills/skills.json --output out.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

DEFAULT_VIEW_NAME = "repo_skills.html"


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


def load_skills_json(input_path: str | Path) -> dict:
    """Load a ``skills.json`` file into a dict."""
    return json.loads(Path(input_path).read_text(encoding="utf-8"))


def default_output_for(input_path: str | Path) -> Path:
    """Place ``repo_skills.html`` next to its source ``skills.json`` by default."""
    return Path(input_path).resolve().parent / DEFAULT_VIEW_NAME


def write_html(data: dict, output_path: str | Path) -> Path:
    """Render ``data`` and write the HTML page to ``output_path``; return its path."""
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(data), encoding="utf-8")
    return out_path


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
  .repo-badge { font-size:11px; background:#1f6feb22; border:1px solid var(--accent); border-radius:999px; padding:2px 8px; color:var(--accent); align-self:flex-start; }
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
    <label class="ctrl" id="repoFilterWrap" style="display:none">Repo
      <select id="repoFilter"><option value="">All repos</option></select>
    </label>
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
const EXPORT_STEM = DATA.aggregate ? "all_repos" : ((DATA.owner ? DATA.owner + "_" : "") + DATA.repo);
function exportJson() {
  download(new Blob([JSON.stringify(DATA, null, 2)], { type: "application/json" }),
           EXPORT_STEM + "_skills.json");
}
function csvCell(v) { return '"' + String(v).replace(/"/g, '""') + '"'; }
function exportCsv() {
  // In aggregate mode prepend a "repo" column so rows stay attributable.
  const head = (DATA.aggregate ? ["repo"] : []).concat(
    ["name", "description", "path", "url", "metadata", "files"]);
  const rows = [head.join(",")];
  for (const s of DATA.skills) {
    const cells = DATA.aggregate ? [csvCell(s.repo || "")] : [];
    rows.push(cells.concat([
      csvCell(s.name), csvCell(s.description), csvCell(s.path), csvCell(s.url),
      csvCell(JSON.stringify(s.metadata || {})),
      csvCell(s.files.map(f => f.path).join(" | ")),
    ]).join(","));
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
  const parts = [s.name, s.description, s.path, s.repo || "", s.source || "", JSON.stringify(s.metadata || {})];
  for (const f of s.files) parts.push(f.path);
  return parts.join("  ").toLowerCase();
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
    (s.repo ? '<span class="repo-badge" data-repo="' + esc(s.repo) + '">' + esc(s.repo) + "</span>" : "") +
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
const repoFilterEl = document.getElementById("repoFilter");
const repoFilterWrap = document.getElementById("repoFilterWrap");
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
  const repo = repoFilterEl ? repoFilterEl.value : "";
  const list = DATA.skills
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => (!repo || s.repo === repo) && terms.every(t => s._hay.includes(t)));
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
if (DATA.aggregate) {
  // Combined view across many registered repos.
  const repos = Array.isArray(DATA.repos) ? DATA.repos : [];
  document.getElementById("title").innerHTML = "All registered skills";
  document.getElementById("subtitle").textContent =
    DATA.count + " skill(s) across " + repos.length + " repo(s)";
  // Populate and reveal the repo filter, sorted by repo name.
  repoFilterWrap.style.display = "flex";
  for (const r of repos.slice().sort((a, b) => a.repo.toLowerCase().localeCompare(b.repo.toLowerCase()))) {
    const opt = document.createElement("option");
    opt.value = r.repo;
    opt.textContent = r.repo + " (" + r.count + ")";
    repoFilterEl.appendChild(opt);
  }
  repoFilterEl.addEventListener("change", resetAndRender);
} else if (DATA.local) {
  // Local clone: no GitHub URL to link to, so show the source path as plain text.
  document.getElementById("title").innerHTML = "Skills in " + esc(DATA.repo);
} else {
  document.getElementById("title").innerHTML =
    'Skills in <a href="https://github.com/' + esc(DATA.owner) + "/" + esc(DATA.repo) +
    '" target="_blank" rel="noopener">' + esc(DATA.owner) + "/" + esc(DATA.repo) + "</a>";
}
if (!DATA.aggregate) {
  document.getElementById("subtitle").textContent =
    DATA.count + " skill(s) · " + (DATA.local ? "local clone: " + (DATA.source || DATA.repo)
                                              : "branch/ref: " + DATA.ref);
}
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
  if (btn) { downloadSkillZip(DATA.skills[Number(btn.dataset.zip)]); return; }
  // Clicking a repo badge (aggregate view) filters down to that repo.
  const badge = e.target.closest(".repo-badge[data-repo]");
  if (badge && DATA.aggregate && repoFilterEl) {
    repoFilterEl.value = badge.dataset.repo;
    resetAndRender();
  }
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
        description="Render a self-contained repo_skills.html from a skills.json scan result."
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to a skills.json file (produced by scan_repo.py).",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output HTML file (default: repo_skills.html next to the input JSON).",
    )
    args = parser.parse_args(argv)

    try:
        data = load_skills_json(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading {args.input}: {exc}", file=sys.stderr)
        return 1

    output = args.output or default_output_for(args.input)
    out_path = write_html(data, output)
    print(
        f"Rendered {data.get('count', len(data.get('skills', [])))} skill(s) "
        f"to {out_path}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
