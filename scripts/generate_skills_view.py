"""Render a self-contained ``repo_skills.html`` from a ``skills.json`` scan result.

This is the *rendering* half of the original ``scan_repo_skills.py``: it does no
scanning and never touches the network. It takes the ``skills.json`` produced by
``scan_repo.py`` and emits one static HTML page that lets you search/filter the
skills and export the listing as JSON/CSV.

The page is intentionally one file with no external dependencies: all styles are
inline, so it works offline with no CDN. Each skill card shows its type (plugin vs.
standalone) and an Expand/Collapse "Installation Steps" section driven by the
``type``/``plugin``/``installation`` fields in ``skills.json``.

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


# The page is intentionally one file with no external dependencies: all styles
# are inline, so it works offline with no CDN.
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  /* Dark theme is the default; the light palette below overrides these when
     <html data-theme="light"> is set. Every color routes through a variable so
     switching themes is just swapping this block of values. */
  :root {
    --bg:#0d1117; --card:#161b22; --border:#30363d; --fg:#e6edf3; --muted:#8b949e;
    --accent:#2f81f7; --accent2:#238636;
    --tag-bg:#21262d; --code-bg:#0d1117; --code-fg:#e6edf3;
    --warn:#d29922; --shadow:rgba(0,0,0,.45);
  }
  [data-theme="light"] {
    --bg:#f4f5f7; --card:#ffffff; --border:#dfe2e8; --fg:#20242e; --muted:#69707c;
    --accent:#5257e8; --accent2:#1f8a45;
    --tag-bg:#eef0f3; --code-bg:#1c2333; --code-fg:#e6edf3;
    --warn:#9a6700; --shadow:rgba(60,66,87,.12);
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background:var(--bg); color:var(--fg); transition:background .2s ease, color .2s ease; }
  header { padding:24px 20px 12px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--bg); z-index:5; }
  h1 { margin:0 0 4px; font-size:20px; }
  h1 a { color:var(--accent); text-decoration:none; }
  .meta { color:var(--muted); font-size:13px; }
  .warn { color:var(--warn); font-size:13px; margin-top:6px; }
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
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px; transition:border-color .15s ease, box-shadow .15s ease, transform .15s ease; }
  .card:hover { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent), 0 8px 22px var(--shadow); transform:translateY(-2px); }
  .card-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .card h2 { margin:0; font-size:16px; }
  .card h2 a { color:var(--fg); text-decoration:none; transition:color .12s ease; }
  .card h2 a:hover, .card h2 a:focus-visible { color:var(--accent); text-decoration:underline; outline:none; }
  .card .desc { color:var(--fg); font-size:13px; line-height:1.45; }
  .desc-toggle { background:none; border:none; padding:0 0 0 4px; margin:0; color:var(--accent); cursor:pointer; font-size:13px; }
  .desc-toggle:hover { text-decoration:underline; border-color:transparent; }
  .card .path { color:var(--muted); font-size:12px; word-break:break-all; }
  .card .path a { color:var(--accent); text-decoration:none; }
  .tags { display:flex; flex-wrap:wrap; gap:5px; }
  .tag { font-size:11px; background:var(--tag-bg); border:1px solid var(--border); border-radius:999px; padding:2px 8px; color:var(--muted); }
  .repo-badge { font-size:11px; background:#1f6feb22; border:1px solid var(--accent); border-radius:999px; padding:2px 8px; color:var(--accent); align-self:flex-start; }
  details { font-size:12px; color:var(--muted); }
  details summary { cursor:pointer; }
  details ul { margin:6px 0 0; padding-left:18px; }
  .install-type { margin:8px 0 4px; font-size:12px; color:var(--muted); }
  .install-type b { color:var(--fg); font-weight:600; }
  pre.install-steps { margin:4px 0 0; padding:9px 11px; background:var(--code-bg); border:1px solid var(--border); border-radius:6px; overflow-x:auto; font-size:12px; line-height:1.5; color:var(--code-fg); white-space:pre; font-family: ui-monospace, "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
  .type-badge { font-size:11px; border-radius:999px; padding:1px 8px; border:1px solid var(--border); white-space:nowrap; }
  .type-badge.plugin { background:#8957e522; border-color:#8957e5; color:#a371f7; }
  .type-badge.standalone { background:#23863622; border-color:var(--accent2); color:#3fb950; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
  #themeToggle { display:inline-flex; align-items:center; gap:5px; }
  /* View toggle: a small segmented control (Table | Cards). */
  .viewtoggle { display:inline-flex; }
  .viewtoggle .viewbtn { border-radius:0; }
  .viewtoggle .viewbtn:first-child { border-radius:8px 0 0 8px; }
  .viewtoggle .viewbtn:last-child { border-radius:0 8px 8px 0; border-left:none; }
  .viewbtn.active { background:var(--accent); border-color:var(--accent); color:#fff; }
  .viewbtn.active:hover { border-color:var(--accent); }
  /* Table view */
  .table-wrap { overflow-x:auto; }
  table.skills-table { width:100%; border-collapse:collapse; font-size:13px; }
  .skills-table th { text-align:left; padding:10px 12px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; border-bottom:1px solid var(--border); white-space:nowrap; }
  .skills-table th[data-sort] { cursor:pointer; user-select:none; }
  .skills-table th[data-sort]:hover { color:var(--fg); }
  .sort-ind { font-size:10px; margin-left:4px; }
  .skills-table td { padding:12px; border-bottom:1px solid var(--border); vertical-align:middle; }
  .skills-table tbody tr { cursor:pointer; transition:background .12s ease; }
  .skills-table tbody tr:hover { background:var(--tag-bg); }
  .skills-table td.name { font-weight:600; color:var(--fg); }
  .skills-table td.repo { color:var(--muted); }
  /* Modal (opened when a table row is clicked) */
  .modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.5); display:none; align-items:flex-start; justify-content:center; z-index:50; padding:40px 16px; overflow-y:auto; }
  .modal-backdrop.open { display:flex; }
  body.modal-open { overflow:hidden; }
  .modal { background:var(--card); border:1px solid var(--border); border-radius:12px; max-width:640px; width:100%; padding:22px 24px; box-shadow:0 12px 40px var(--shadow); }
  .modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .modal-head h2 { margin:0; font-size:22px; }
  .modal-head h2 a { color:var(--fg); text-decoration:none; }
  .modal-head h2 a:hover { color:var(--accent); text-decoration:underline; }
  .modal-close { background:none; border:none; color:var(--accent); font-size:24px; line-height:1; cursor:pointer; padding:0 2px; }
  .modal .badges { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:12px 0; color:var(--muted); font-size:13px; }
  .modal .modal-desc { font-size:14px; line-height:1.55; color:var(--fg); white-space:pre-wrap; }
  .modal .steps-head { display:flex; align-items:center; justify-content:space-between; margin:20px 0 8px; }
  .modal .steps-head h3 { margin:0; font-size:16px; }
  .copy-btn { background:none; border:none; color:var(--accent); cursor:pointer; font-size:14px; padding:0; }
  .copy-btn:hover { text-decoration:underline; }
</style>
<script>
  /* Apply the saved (or OS-preferred) theme before first paint to avoid a flash
     of the wrong theme. The toggle handler further down keeps it in sync. */
  (function () {
    try {
      var t = localStorage.getItem("skills-theme");
      if (t !== "light" && t !== "dark") {
        t = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
      }
      document.documentElement.setAttribute("data-theme", t);
    } catch (e) { /* localStorage blocked -> fall back to CSS default (dark) */ }
  })();
</script>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="meta" id="subtitle"></div>
  <div class="warn" id="truncated" style="display:none"></div>
  <div class="toolbar">
    <span class="viewtoggle">
      <button id="viewTable" class="viewbtn" type="button">☰ Table</button>
      <button id="viewCards" class="viewbtn" type="button">▦ Cards</button>
    </span>
    <input type="search" id="filter" placeholder="Filter by name, description, path, metadata, or file…" autofocus>
    <label class="ctrl" id="repoFilterWrap" style="display:none">Repo
      <select id="repoFilter"><option value="">All repos</option></select>
    </label>
    <label class="ctrl">Sort
      <select id="sort">
        <option value="name-asc">Name (A→Z)</option>
        <option value="name-desc">Name (Z→A)</option>
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
    <button id="themeToggle" type="button" title="Toggle day / night mode" aria-label="Toggle day / night mode"></button>
    <span class="count" id="count"></span>
  </div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="table-wrap" id="tableWrap" style="display:none">
    <table class="skills-table">
      <thead><tr id="tableHead"></tr></thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
  <div class="empty" id="empty" style="display:none">No skills match your filter.</div>
  <div class="pager" id="pager" style="display:none">
    <button id="prev">‹ Prev</button>
    <span id="pageInfo"></span>
    <button id="next">Next ›</button>
  </div>
</main>

<div class="modal-backdrop" id="modal" role="dialog" aria-modal="true" aria-hidden="true">
  <div class="modal" id="modalPanel"></div>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
    ["name", "description", "path", "url", "metadata"]);
  const rows = [head.join(",")];
  for (const s of DATA.skills) {
    const cells = DATA.aggregate ? [csvCell(s.repo || "")] : [];
    rows.push(cells.concat([
      csvCell(s.name), csvCell(s.description), csvCell(s.path), csvCell(s.url),
      csvCell(JSON.stringify(s.metadata || {})),
    ]).join(","));
  }
  // UTF-8 BOM so Excel reads non-ASCII correctly.
  download(new Blob(["﻿" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" }),
           EXPORT_STEM + "_skills.csv");
}

/* ---- rendering ---------------------------------------------------------- */
const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Full "owner/repo" label: repos can come from different owners, so the short
// repo name alone can collide. Falls back to the short name for local scans.
const repoLabel = s => s.source || s.repo || "";

// Frontmatter keys we never surface as tags (kept out of the page on purpose).
const META_HIDDEN = new Set(["license", "allowed-tools", "allowed_tools", "argument-hint", "argument_hint"]);

// Flatten a skill to one lowercase string for substring filtering.
function haystack(s) {
  const parts = [s.name, s.description, s.path, s.repo || "", s.source || "",
                 s.type || "", s.plugin || "", JSON.stringify(s.metadata || {})];
  return parts.join("  ").toLowerCase();
}
DATA.skills.forEach(s => { s._hay = haystack(s); });

function metaTags(md) {
  if (!md || typeof md !== "object") return "";
  const tags = [];
  for (const [k, v] of Object.entries(md)) {
    if (META_HIDDEN.has(String(k).toLowerCase())) continue;
    const val = Array.isArray(v) ? v.join(", ") : (typeof v === "object" ? JSON.stringify(v) : v);
    tags.push('<span class="tag">' + esc(k) + ": " + esc(val) + "</span>");
  }
  return tags.length ? '<div class="tags">' + tags.join("") + "</div>" : "";
}

// Expand/Collapse section showing how to install the skill: a type line
// (plugin vs. standalone) followed by the copy-pasteable steps from skills.json.
function installSteps(s) {
  const steps = Array.isArray(s.installation) ? s.installation : [];
  const typeLine = s.type === "plugin"
    ? "<b>Plugin skill</b>" + (s.plugin ? " — ships with plugin <b>" + esc(s.plugin) + "</b>" : "")
    : "<b>Standalone skill</b>";
  const body = steps.length
    ? '<pre class="install-steps">' + steps.map(esc).join("\n") + "</pre>"
    : '<p class="install-type">No installation steps available.</p>';
  return "<details><summary>Installation Steps</summary>" +
    '<div class="install-type">' + typeLine + "</div>" + body + "</details>";
}

// Small pill showing the skill's type at a glance, above the card body.
function typeBadge(s) {
  if (!s.type) return "";
  return '<span class="type-badge ' + esc(s.type) + '">' + esc(s.type) + "</span>";
}

// Descriptions longer than this are clamped, with a "…more" toggle to reveal the
// rest. The full text lives in data-full so search/CSV still see everything.
const DESC_LIMIT = 400;
function descHtml(s) {
  const full = s.description || "";
  if (!full) return "";
  if (full.length <= DESC_LIMIT) return '<div class="desc">' + esc(full) + "</div>";
  const short = full.slice(0, DESC_LIMIT).replace(/\s+$/, "");
  return '<div class="desc" data-full="' + esc(full) + '">' +
    '<span class="desc-text">' + esc(short) + "</span>" +
    '<button type="button" class="desc-toggle" data-expanded="0">…more</button></div>';
}

// Turn a SKILL.md file URL into its containing *folder* URL: drop the trailing
// filename, and (for GitHub) swap the file-view "/blob/" for the dir-view "/tree/".
function folderUrl(s) {
  if (!s.url) return "";
  return s.url.replace(/\/[^\/]*$/, "").replace("/blob/", "/tree/");
}

function card(s, i) {
  // The skill name links to its source folder on GitHub (the skill directory,
  // not the SKILL.md file). Local scans expose a file:// folder URL instead;
  // fall back to plain text when no URL is available.
  const dirUrl = folderUrl(s);
  const nameHtml = dirUrl
    ? '<a href="' + esc(dirUrl) + '" target="_blank" rel="noopener" title="Open ' + esc(s.name) + ' skill folder on GitHub">' + esc(s.name) + "</a>"
    : esc(s.name);
  return '<div class="card">' +
    '<div class="card-head"><h2>' + nameHtml + "</h2>" + typeBadge(s) + "</div>" +
    (s.repo ? '<span class="repo-badge" data-source="' + esc(repoLabel(s)) + '">' + esc(repoLabel(s)) + "</span>" : "") +
    descHtml(s) +
    '<div class="path"><a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.path) + "</a></div>" +
    metaTags(s.metadata) +
    installSteps(s) +
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
const tableWrap = document.getElementById("tableWrap");
const tableHead = document.getElementById("tableHead");
const tableBody = document.getElementById("tableBody");
const viewTableBtn = document.getElementById("viewTable");
const viewCardsBtn = document.getElementById("viewCards");
const modalEl = document.getElementById("modal");
const modalPanel = document.getElementById("modalPanel");

let page = 1;  // 1-based; reset to 1 whenever the matched set changes.
// Cards is the default view; a saved choice (or last click) overrides it.
let view = "cards";
try { const v = localStorage.getItem("skills-view"); if (v === "table" || v === "cards") view = v; } catch (e) { /* ignore */ }

// Comparators keyed by the Sort dropdown's value. Each pair is {s, i} where
// i is the original index into DATA.skills.
const SORTERS = {
  "name-asc":   (a, b) => a.s.name.toLowerCase().localeCompare(b.s.name.toLowerCase()),
  "name-desc":  (a, b) => b.s.name.toLowerCase().localeCompare(a.s.name.toLowerCase()),
};

function matched() {
  const q = filterEl.value.trim().toLowerCase();
  const terms = q ? q.split(/\s+/) : [];
  const repo = repoFilterEl ? repoFilterEl.value : "";
  const list = DATA.skills
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => (!repo || repoLabel(s) === repo) && terms.every(t => s._hay.includes(t)));
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

  // Same filtered/sorted/paginated slice, rendered as cards or a table row set.
  if (view === "table") {
    grid.style.display = "none";
    tableWrap.style.display = list.length ? "block" : "none";
    renderTable(slice);
  } else {
    tableWrap.style.display = "none";
    grid.style.display = "grid";
    grid.innerHTML = slice.map(({ s, i }) => card(s, i)).join("");
  }
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

/* ---- table view -------------------------------------------------------- */
// Columns: Name (sortable) · Type · Repository (only when aggregating repos,
// where the value actually varies). Each row carries data-idx into DATA.skills
// so a click can open the detail modal.
function renderTable(slice) {
  const dir = sortEl.value === "name-desc" ? "▼" : "▲";
  tableHead.innerHTML =
    '<th data-sort="name">Name <span class="sort-ind">' + dir + "</span></th>" +
    "<th>Type</th>" +
    (DATA.aggregate ? "<th>Repository</th>" : "");
  tableBody.innerHTML = slice.map(({ s, i }) =>
    '<tr data-idx="' + i + '">' +
      '<td class="name">' + esc(s.name) + "</td>" +
      "<td>" + typeBadge(s) + "</td>" +
      (DATA.aggregate ? '<td class="repo">' + esc(repoLabel(s)) + "</td>" : "") +
    "</tr>"
  ).join("");
}

/* ---- detail modal ------------------------------------------------------ */
function copyText(text, btn) {
  const done = () => { btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  const fail = () => { btn.textContent = "Copy failed"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done, fail));
  } else {
    fallbackCopy(text, done, fail);
  }
}
// file:// pages often can't use the async clipboard API; fall back to a hidden
// textarea + execCommand so Copy still works when opened straight from disk.
function fallbackCopy(text, done, fail) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    ok ? done() : fail();
  } catch (e) { fail(); }
}

function openModal(idx) {
  const s = DATA.skills[idx];
  if (!s) return;
  const dirUrl = folderUrl(s);
  const nameHtml = dirUrl
    ? '<a href="' + esc(dirUrl) + '" target="_blank" rel="noopener">' + esc(s.name) + "</a>"
    : esc(s.name);
  const pluginTxt = (s.type === "plugin" && s.plugin)
    ? "<span>plugin: " + esc(s.plugin) + "</span>" : "";
  const steps = Array.isArray(s.installation) ? s.installation : [];
  const stepsBlock = steps.length
    ? '<div class="steps-head"><h3>Installation steps</h3>' +
        '<button type="button" class="copy-btn" id="copySteps">Copy</button></div>' +
        '<pre class="install-steps">' + steps.map(esc).join("\n") + "</pre>"
    : "";
  const pathBlock = s.url
    ? '<div class="path" style="margin-top:14px"><a href="' + esc(s.url) +
        '" target="_blank" rel="noopener">' + esc(s.path) + "</a></div>"
    : '<div class="path" style="margin-top:14px">' + esc(s.path || "") + "</div>";

  modalPanel.innerHTML =
    '<div class="modal-head"><h2>' + nameHtml + "</h2>" +
      '<button type="button" class="modal-close" id="modalClose" aria-label="Close">×</button></div>' +
    '<div class="badges">' + typeBadge(s) + pluginTxt +
      (s.repo ? '<span class="repo-badge">' + esc(repoLabel(s)) + "</span>" : "") + "</div>" +
    '<div class="modal-desc">' + esc(s.description || "") + "</div>" +
    pathBlock + stepsBlock;

  modalEl.classList.add("open");
  modalEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");

  document.getElementById("modalClose").addEventListener("click", closeModal);
  const copyBtn = document.getElementById("copySteps");
  if (copyBtn) copyBtn.addEventListener("click", () => copyText(steps.join("\n"), copyBtn));
}

function closeModal() {
  modalEl.classList.remove("open");
  modalEl.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

/* ---- view switch ------------------------------------------------------- */
function applyView(v) {
  view = v;
  viewTableBtn.classList.toggle("active", v === "table");
  viewCardsBtn.classList.toggle("active", v === "cards");
  render();
}

/* ---- wire up ------------------------------------------------------------ */
if (DATA.aggregate) {
  // Combined view across many registered repos.
  const repos = Array.isArray(DATA.repos) ? DATA.repos : [];
  document.getElementById("title").innerHTML = "All registered skills";
  document.getElementById("subtitle").textContent =
    DATA.count + " skill(s) across " + repos.length + " repo(s)";
  // Populate and reveal the repo filter, sorted by repo name.
  repoFilterWrap.style.display = "flex";
  const repoName = r => r.source || r.repo || "";
  for (const r of repos.slice().sort((a, b) => repoName(a).toLowerCase().localeCompare(repoName(b).toLowerCase()))) {
    const opt = document.createElement("option");
    opt.value = repoName(r);
    opt.textContent = repoName(r) + " (" + r.count + ")";
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

/* ---- day / night theme toggle ------------------------------------------- */
// The head script already applied the initial theme; here we just label the
// button to match and let clicks flip + persist the choice.
const themeToggleEl = document.getElementById("themeToggle");
function currentTheme() { return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; }
function syncThemeButton() {
  // Show the theme you'd switch *to*, matching common toggle UX.
  themeToggleEl.textContent = currentTheme() === "light" ? "🌙 Dark" : "☀️ Light";
}
syncThemeButton();
themeToggleEl.addEventListener("click", () => {
  const next = currentTheme() === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("skills-theme", next); } catch (e) { /* ignore */ }
  syncThemeButton();
});
grid.addEventListener("click", e => {
  // Expand/collapse a clamped description.
  const toggle = e.target.closest(".desc-toggle");
  if (toggle) {
    const wrap = toggle.closest(".desc");
    const textEl = wrap.querySelector(".desc-text");
    if (toggle.dataset.expanded === "1") {
      textEl.textContent = wrap.dataset.full.slice(0, DESC_LIMIT).replace(/\s+$/, "");
      toggle.textContent = "…more";
      toggle.dataset.expanded = "0";
    } else {
      textEl.textContent = wrap.dataset.full;
      toggle.textContent = " show less";
      toggle.dataset.expanded = "1";
    }
    return;
  }
  // Clicking a repo badge (aggregate view) filters down to that repo.
  const badge = e.target.closest(".repo-badge[data-source]");
  if (badge && DATA.aggregate && repoFilterEl) {
    repoFilterEl.value = badge.dataset.source;
    resetAndRender();
  }
});

/* ---- table + modal + view-toggle events -------------------------------- */
viewTableBtn.addEventListener("click", () => { applyView("table"); try { localStorage.setItem("skills-view", "table"); } catch (e) {} });
viewCardsBtn.addEventListener("click", () => { applyView("cards"); try { localStorage.setItem("skills-view", "cards"); } catch (e) {} });
// Clicking the Name header toggles the shared sort order (asc <-> desc).
tableHead.addEventListener("click", e => {
  if (!e.target.closest("th[data-sort]")) return;
  sortEl.value = sortEl.value === "name-asc" ? "name-desc" : "name-asc";
  render();
});
// A row click opens the detail modal (ignore clicks on links inside the row).
tableBody.addEventListener("click", e => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr[data-idx]");
  if (tr) openModal(parseInt(tr.dataset.idx, 10));
});
// Close the modal by clicking the backdrop or pressing Escape.
modalEl.addEventListener("click", e => { if (e.target === modalEl) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && modalEl.classList.contains("open")) closeModal(); });

applyView(view);  // sets the toggle's active button and does the first render
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
