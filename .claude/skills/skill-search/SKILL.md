---
name: skill-search
description: Search for Claude skills across all supported skill sources and display structured results grouped by source type.
---

## Supported sources include:

* Global skills
* Project skills
* Plugin-installed skills

---

## Supported Skill Locations

### Global Skills

The search root for the Global tier is the **user's home directory** (the path `~` resolves to). From that root, the global skills folder is:

```bash id="0a9d4v"
~/.claude/skills/
```

For example, on Windows this resolves to `C:\Users\<username>\.claude\skills\`; on macOS/Linux to `/Users/<username>/.claude/skills/` or `/home/<username>/.claude/skills/`.

Recursively search **every subdirectory** of that root for `SKILL.md` files. Each directory containing a `SKILL.md` represents one global skill — match against both the containing directory name and the `name:` field in the SKILL.md frontmatter.

**Important — do not bake the search term into the path glob.** A pattern like `**/*<term>*/SKILL.md` misses skills whose folder name *equals* the term, and depends fragilely on what's inside the folder. Instead:

1. Enumerate **all** `SKILL.md` files under the global root in a single pass (e.g. glob `~/.claude/skills/**/SKILL.md`).
2. For each result, extract the parent directory name and read the `name:` line from the frontmatter.
3. Filter the list by case-insensitive substring match against the query, on either the directory name or the metadata name.

If `~/.claude/skills/` does not exist, treat the Global tier as empty (do not error).

### Project Skills

The search root for the Project tier is the **current working directory** — the directory Claude Code was launched in (its `cwd`). From that root, the project skills folder is:

```bash id="wkn5p0"
<cwd>/.claude/skills/
```

For example, if Claude Code is running in `C:\projects\idmtools_version`, the Project tier root is `C:\projects\idmtools_version\.claude\skills\`.

Recursively search **every subdirectory** of that root for `SKILL.md` files. Each directory containing a `SKILL.md` represents one project skill — match against both the containing directory name and the `name:` field in the SKILL.md frontmatter.

**Important — do not bake the search term into the path glob.** A pattern like `**/*<term>*/SKILL.md` misses skills whose folder name *equals* the term, and depends fragilely on what's inside the folder. Instead:

1. Enumerate **all** `SKILL.md` files under the project root in a single pass (e.g. glob `<cwd>/.claude/skills/**/SKILL.md`).
2. For each result, extract the parent directory name and read the `name:` line from the frontmatter.
3. Filter the list by case-insensitive substring match against the query, on either the directory name or the metadata name.

If the project root or its `.claude/skills/` subdirectory does not exist, treat the Project tier as empty (do not error).

### Plugin Skills

```bash id="xbynz6"
~/.claude/plugins/marketplaces/*/skills/*/
```

---

## Behavior

### Search Rules

* Accept a skill name as input.
* Search recursively through all supported skill locations.
* Match skills by:

  * directory name
  * metadata name
* Matching must be:

  * case-insensitive
  * partial-match friendly

---

## Output Format

Results must be grouped into the following sections:

* Global
* Project
* Plugin

For every matching skill, output:

* Name
* Full path
* Description
* Status:

  * Enabled
  * Disabled

Additionally:

* If the skill is a Plugin skill, also output:

  * Plugin name

* If the skill is disabled, also output:

  * Claude Code command to enable the skill

* Provide one or two example prompts demonstrating how the discovered skill can be used in Claude Code.

Use the following structure:

```text id="u1rk7o"
--------------------------------------------------
Name: <skill-name>

Plugin: <plugin-name>   # Only for Plugin skills

Path: <full-path>

Status:
Enabled

Description:
<skill-description>

Usage Examples:
1. <example prompt>
2. <example prompt>
--------------------------------------------------
```

For disabled skills:

```text id="kqnh0o"
--------------------------------------------------
Name: <skill-name>

Path: <full-path>

Status:
Disabled

Enable Command:
claude skills enable <skill-name>

Description:
<skill-description>

Usage Examples:
1. <example prompt>
2. <example prompt>
--------------------------------------------------
```

---

## Skill Status Detection

Determine whether a skill is enabled or disabled using available Claude configuration and metadata sources.

Possible indicators may include:

* Claude configuration files
* Plugin configuration
* Skill manifest metadata
* Disabled flags
* Symbolic links
* Registration status
* Workspace configuration

If the status cannot be determined reliably, output:

```text id="11on73"
Status:
Unknown
```

In that case, do not invent an enable command.

---

## Enable Command Rules

* Only output an enable command if the skill status is clearly Disabled.
* Use the canonical Claude Code command format when possible.

Preferred format:

```bash id="yz3vcm"
claude skills enable <skill-name>
```

For plugin-managed skills, include plugin-qualified commands if appropriate.

Example:

```bash id="n6v89q"
claude plugins enable acme/python-linter
```

Do not invent unsupported commands.

---

## Usage Example Rules

* The usage examples should demonstrate realistic Claude Code usage.
* Tailor the examples to the purpose of the discovered skill.
* Infer likely usage patterns from:

  * skill name
  * description
  * README.md
  * metadata
* Keep examples concise and actionable.
* Prefer natural-language prompts a user would actually type into Claude Code.

Example:

```text id="jlwmf6"
Usage Examples:
1. Refactor this Python module using the python-helper skill.
2. Use python-helper to generate pytest tests for utils.py.
```

---

## Metadata Sources

Descriptions, usage hints, and status information may be extracted from:

* README.md
* skill.json
* manifest.json
* example files
* prompt templates
* plugin metadata
* Claude configuration files
* workspace settings
* other metadata files

---

## No Match Handling

If no matching skill exists, output:

```text id="qldv1d"
No such skill with the name: <input-name>
```

---

## Additional Rules

* Ignore invalid or broken skill folders gracefully.
* Do not fail if a skill source directory does not exist.
* Sort results alphabetically within each group.
* Avoid duplicate results.
* Prefer concise descriptions.
* If a group contains no matches, still display the group header followed by:

```text id="h2v2ca"
No matches found.
```

---

## Example Output

```text id="s9u7f9"
=== Plugin ===

--------------------------------------------------
Name: python-linter
Plugin: acme
Path: ~/.claude/plugins/marketplaces/acme/skills/python-linter

Status:
Disabled

Enable Command:
claude plugins enable acme/python-linter

Description:
Provides linting and formatting workflows for Python projects.

Usage Examples:
1. Use python-linter to check this repository for Ruff and Black issues.
2. Fix formatting problems in src/app.py using python-linter.
--------------------------------------------------
```
