# skill-search — Discovery Bug Fix Session

**Date:** 2026-05-09
**Working directory:** `C:\projects\idmtools_version`
**Files affected:**
- `C:\Users\zhaoweidu\.claude\skills\skill-search\SKILL.md` (global copy)
- `C:\projects\idmtools_version\.claude\skills\skill-search\SKILL.md` (project copy)

---

## 1. Initial state

The `skill-search` skill exists in two tiers:

| Tier | Path |
|---|---|
| Global | `C:\Users\zhaoweidu\.claude\skills\skill-search\SKILL.md` |
| Project | `C:\projects\idmtools_version\.claude\skills\skill-search\SKILL.md` |

When `/skill-search` is invoked from this repo, the harness loads the **global** copy (confirmed by inspecting which sections appeared expanded in subsequent invocations). Earlier in this session I had assumed the project copy would shadow the global; that turned out to be incorrect.

Both files originally described their three search tiers with terse one-line locations:

```
### Global Skills
~/.claude/skills/

### Project Skills
.claude/skills/

### Plugin Skills
~/.claude/plugins/marketplaces/*/skills/*/
```

The Search Rules section said only "Search recursively through all supported skill locations."

---

## 2. The bug

While running five `/skill-search` queries (`review`, `quality-checker`, `code-review`, `search`, `install`), I implemented the recursive search using a Glob pattern that baked the search term directly into the path:

```
<root>/**/*<term>*/**
<root>/**/*<term>*/SKILL.md
```

For the query `search`, this missed the **Project tier** `skill-search` skill — even though its folder name `skill-search` plainly contains "search". The pattern requires:

1. A folder whose name contains the search term.
2. *Something inside* that folder for `**` to resolve against.

When the term-matching folder is the leaf directory (or its layout is unusual), the inner `**` segment may not produce a match. Result: `=== Project === No matches found.` was reported in all five queries, even though the project tier had four skills (`convert_sphinx_to_mkdocs`, `docs-new-section`, `generate-mkdocs`, `skill-search`) and the `search` query had a legitimate match.

The same anti-pattern affected the Plugin tier earlier (`quality-checker` had to be located via an exact path lookup after the `**` glob came up empty).

---

## 3. Root cause

Mixing **enumeration** and **filtering** into a single glob pattern is fragile. The correct decomposition is:

1. **Enumerate** every `SKILL.md` file under each tier root (one broad glob per tier).
2. **Filter** the resulting paths in a second pass by:
   - directory-name substring match
   - frontmatter `name:` field substring match

This matches what the Search Rules already said in spirit ("Search recursively through all supported skill locations") but the implementation guidance was missing.

---

## 4. Fixes applied

### Fix 1 — Project file, Project Skills section

**File:** `C:\projects\idmtools_version\.claude\skills\skill-search\SKILL.md`

Replaced the original terse block:

```
### Project Skills

.claude/skills/
```

with an expanded section that:
- Names the cwd as the search root, with a worked example using this repo.
- Specifies recursion across every subdirectory.
- Specifies matching against both directory name and `name:` frontmatter.
- Adds an explicit warning *not* to bake the search term into the glob.
- Prescribes the enumerate-then-filter algorithm in numbered steps.
- Specifies graceful handling when `.claude/skills/` doesn't exist.

### Fix 2 — Both files, Global Skills section (parallel edits)

**Files:**
- `C:\Users\zhaoweidu\.claude\skills\skill-search\SKILL.md`
- `C:\projects\idmtools_version\.claude\skills\skill-search\SKILL.md`

Same expansion pattern applied to the Global Skills section in both files:
- Names `~` (user home) as the resolution root.
- Cross-platform examples (Windows + POSIX path forms).
- Same recursion + matching + enumerate-first algorithm.
- Same graceful-empty-tier handling.

### Fix 3 — Global file, Project Skills section (sync edit)

**File:** `C:\Users\zhaoweidu\.claude\skills\skill-search\SKILL.md`

Brought the global file's Project Skills section to the same expanded form as the project file's, so the two files are byte-identical.

---

## 5. Verification

```
diff "C:\Users\zhaoweidu\.claude\skills\skill-search\SKILL.md" \
     "C:\projects\idmtools_version\.claude\skills\skill-search\SKILL.md"
→ FILES ARE IDENTICAL
```

A subsequent `/skill-search search` invocation correctly reported both copies of the `skill-search` skill (Global tier + Project tier), confirming the algorithm fix took effect.

---

## 6. State after fixes

| Section | Project file | Global file |
|---|---|---|
| Global Skills | Expanded (cwd + algorithm) | Expanded (cwd + algorithm) |
| Project Skills | Expanded (cwd + algorithm) | Expanded (cwd + algorithm) |
| Plugin Skills | **Unchanged** (terse one-liner) | **Unchanged** (terse one-liner) |

The Plugin tier was left as-is. It has more inherent complexity (marketplace + plugin folder + nested `skills/` directory + marketplace-vs-cache duplicate suppression + the unusual single-skill flat layout used by `idm-pkg-install`), so its expansion was deferred pending a separate request.

---

## 7. Lessons recorded

1. **Files-on-disk ≠ skill enabled.** A SKILL.md being present in `~/.claude/plugins/marketplaces/<m>/<p>/skills/...` only means the marketplace was registered. The plugin must be in `installed_plugins.json` *and* `enabledPlugins` in `settings.json` for its skills to surface in the available-skills list.

2. **Resolution order for same-named skills.** When `skill-search` exists in both Global and Project tiers, the harness loads the **Global** copy for the prompt body. Keeping the two in sync (via this session's edits) means the user's experience is identical regardless of which copy gets loaded.

3. **Enumerate, then filter.** Don't bake search terms into glob patterns. Use one broad glob per tier (`<root>/**/SKILL.md`), then filter the result list in code or via a second `Grep` pass. This is now codified in the SKILL.md.

4. **Verify sync with `diff`, not by re-reading.** The `diff … && echo IDENTICAL || echo DIFFER` idiom catches whitespace/encoding differences that visual comparison misses.

5. **Two-source name matching.** Match on both the *directory name* (cheap, structural) and the *frontmatter `name:`* field (authoritative, but requires reading the file). They usually agree, but can drift if a folder is renamed without updating frontmatter.
