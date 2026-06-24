# Repo Skills Scanner

Find every Claude **skill** in a repository and turn it into a single,
self-contained HTML page you can search, filter, export, and download from —
with no server and no internet connection once it's built.

A *skill* is any directory containing a `SKILL.md` file. This toolset scans a
repo (a **GitHub repo** via the API — no clone — or an **already-cloned local
directory**), embeds each skill's files into the page as base64, and produces an
offline-capable `repo_skills.html`. Multiple repos can be **registered** and then
merged into one combined `all_skills.html`.

## Install

```bash
pip install -r requirements.txt
```

Requires Python 3.10+. `PyYAML` is optional (the scanner falls back to a regex
parser without it), but recommended for correct list/dict frontmatter handling.

To raise the GitHub API rate limit (60 → 5000 req/hr) and scan private repos, set
a token:

```bash
export GITHUB_TOKEN=ghp_xxx        # Windows PowerShell: $env:GITHUB_TOKEN="ghp_xxx"
```

## Quick start

```bash
# Register one repo: scan it, build its page, and record it in the registry.
python scripts/register_repo.py --repo anthropics/skills
#   -> repos/skills/skills.json
#   -> repos/skills/repo_skills.html

# Build the combined page across every registered repo.
python scripts/generate_all_skills_view.py
#   -> repos/all_skills.html

# Later, refresh everything at once.
python scripts/rescan_all.py
```

Open any generated `.html` file directly in a browser — it is fully standalone.

## Scripts

The toolset is split into small, single-purpose scripts. Each is runnable on its
own *and* importable as a library; the higher-level scripts simply call the
lower-level functions in-process.

| Script | Role |
|---|---|
| `register_repo.py` | **Main entry point.** Scan a repo, build its page, and record it in the registry. |
| `rescan_all.py` | Re-scan every registered repo, then rebuild the combined `all_skills.html`. |
| `generate_all_skills_view.py` | Merge registered repos' scans into one combined page. |
| `registry.py` | Manage `repos/registry.json` (the list of registered repos). |
| `scan_repo.py` | Low-level: scan one repo → `skills.json`. |
| `generate_skills_view.py` | Low-level: render one `skills.json` → `repo_skills.html`. |

> `scan_repo_skills.py` is the original all-in-one script, kept as a reference.

### `register_repo.py`

```bash
python scripts/register_repo.py --repo <owner/repo | url | /tree/<ref> url | local-path>
                                [--repos-dir repos]
                                [--max-file-bytes N]   # skip embedding files larger than N (default 5 MiB)
                                [--no-view]             # scan only, skip the HTML page
                                [--no-register]         # scan without recording in the registry
```

### `rescan_all.py`

```bash
python scripts/rescan_all.py [--repos-dir repos]
                             [--max-file-bytes N]
                             [--no-view]        # skip per-repo pages
                             [--no-aggregate]   # refresh scans only, don't rebuild all_skills.html
```

### `generate_all_skills_view.py`

```bash
python scripts/generate_all_skills_view.py [--repos-dir repos]
                                           [--output all_skills.html]
                                           [--include-unregistered]  # include every skills.json on disk
```

By default the combined page includes **only registered repos**; stray scan
folders are ignored unless you pass `--include-unregistered`.

### `registry.py`

```bash
python scripts/registry.py list
python scripts/registry.py remove <repo-name>
```

### `scan_repo.py` / `generate_skills_view.py` (low-level)

```bash
python scripts/scan_repo.py --repo <ref>                       # -> repos/<repo>/skills.json
python scripts/generate_skills_view.py --input repos/<repo>/skills.json [--output page.html]
```

## Output layout

```
repos/
  registry.json            # which repos are registered
  all_skills.html          # combined page across registered repos
  <repo-name>/
    skills.json            # the scan result (self-contained: file bytes embedded)
    repo_skills.html       # this repo's standalone page
```

`<repo-name>` is the bare repo name (GitHub `owner/repo` → `repo`). Re-scanning a
repo overwrites its folder; re-registering updates its registry entry in place.

## The generated page

Each `repo_skills.html` / `all_skills.html` is one file with inline styles and a
tiny pure-JS ZIP writer, so it works offline. From the page you can:

- **search / filter** skills live (by name, description, path, metadata, file
  names, and — in the combined view — repo);
- **sort** and **paginate**;
- **export** the scan as JSON or CSV;
- **download** any skill's folder as a ZIP, built in the browser from the
  embedded file bytes.

The combined page adds a **Repo** filter and a clickable repo badge on each card.

## `skills.json` schema

The scan result is self-contained — it carries everything needed to build the
HTML, including each file's bytes (base64). This is what makes the combined page
a pure merge with no re-scanning.

```jsonc
{
  "schema_version": 1,
  "scanned_at": "2026-06-24T18:32:10Z",   // UTC, when the scan ran
  "owner": "anthropics",                   // "" for a local clone
  "repo": "skills",                        // basename; the <repo-name> folder
  "ref": "main",                           // branch/tag/sha, or "local clone"
  "local": false,
  "source": "anthropics/skills",           // "owner/repo" or absolute local path
  "truncated": false,                      // GitHub tree truncated (repo too big)?
  "count": 2,
  "skills": [
    {
      "name": "pdf-processing",            // frontmatter name, else folder name
      "folder": "pdf-processing",          // ZIP top-level directory name
      "description": "Extract text and tables from PDF files.",
      "path": "document/pdf-processing/SKILL.md",   // repo-relative path to SKILL.md
      "url": "https://github.com/.../SKILL.md",
      "metadata": { "version": "1.0", "tags": ["pdf"] },  // extra frontmatter
      "files": [
        { "path": "SKILL.md", "url": "...", "size": 1234, "b64": "LS0t..." },
        { "path": "scripts/x.py", "url": "...", "size": 8000000, "b64": null, "skipped": true }
      ]
    }
  ]
}
```

In the **combined** dataset each skill is additionally stamped with `repo`,
`source`, and `repo_local`, and the top level gains `aggregate: true` plus a
`repos` summary list.

`b64` is `null` when a file is unreadable or larger than `--max-file-bytes`; such
files are still listed but excluded from the in-browser ZIP.

## How it fits together

```
register_repo.py ── scan_repo.py ─────────► repos/<repo>/skills.json
       │            generate_skills_view.py ► repos/<repo>/repo_skills.html
       └──────────► registry.py ───────────► repos/registry.json

rescan_all.py ──► (re-scan every registered repo) ──► generate_all_skills_view.py
                                                          └─► repos/all_skills.html
```
