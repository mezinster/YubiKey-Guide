# GitHub Pages Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the guide and the ykrecover docs at `https://mezinster.github.io/YubiKey-Guide/`. The site is built by MkDocs Material from the existing Markdown files and deployed by a workflow.

**Architecture:**

- `site/build.py` (stdlib only) copies the existing Markdown files into `site/.build/`, adapts them and writes a generated download page. It never edits the sources.
- `site/mkdocs.yml` builds `site/.build/` into `site/.site/`.
- `.github/workflows/pages.yml` runs both steps and deploys to GitHub Pages.
- The `lint` workflow runs the unit tests and a strict build on every push and PR.

**Tech Stack:** Python 3.12 stdlib, MkDocs 1.6.1 with Material 9.7.6, GitHub Actions (`actions/configure-pages@v5`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4`).

**Spec:** `docs/superpowers/specs/2026-10-12-github-pages-site-design.md`

## Global Constraints

- **Sources stay untouched.** The source files (`README.md`, `SECENV.md`, `piv/README.md`, `recovery/README.md`, `media/`) are never modified by the build. All changes happen in `site/.build/`.
- **Locations.** All new site files live in `site/`. Build output goes to `site/.build/` and `site/.site/`; both are already git-ignored by the repo's `.*` rule, so never add or whitelist them.
- **Dependencies.** `build.py` and `test_build.py` use the stdlib only. The pip dependencies are `mkdocs-material==9.7.6` and `mkdocs==1.6.1`, in `site/requirements.txt`. Material 9 does not support MkDocs 2.0, so MkDocs is pinned too.
- **URLs.**
  - Fork: `https://github.com/mezinster/YubiKey-Guide`.
  - Site: `https://mezinster.github.io/YubiKey-Guide/`.
  - Upstream: `https://github.com/drduh/YubiKey-Guide`.
  - The fork's default branch is `master`.
- **Page files:**

  | Source | Page file |
  | - | - |
  | `site/home.md` | `index.md` |
  | `README.md` | `guide.md` |
  | `SECENV.md` | `secenv.md` |
  | `piv/README.md` | `piv.md` |
  | `recovery/README.md` | `recovery.md` |
  | generated | `download.md` |

- **Commits.** Short lowercase imperative messages prefixed `docs:` or `ci:`. No `Co-Authored-By` or any other attribution lines.
- **Commands** (run from the repo root):
  - unit tests: `python3 -m unittest discover -s site -v`
  - build: `python3 site/build.py && mkdocs build --strict -f site/mkdocs.yml`
  - lint: `source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && npx --yes markdownlint-cli2@0.23.3`
- **Local MkDocs.** Install it in a venv: `python3 -m venv /tmp/claude-1000/mkd && /tmp/claude-1000/mkd/bin/pip install -r site/requirements.txt`, then use `/tmp/claude-1000/mkd/bin/mkdocs`.

### Ruling made while planning (deviation from the spec text)

**No `release: published` trigger.** `release-windows.yml` creates releases with `GITHUB_TOKEN`, and events caused by that token never start other workflows. A tag-ref run would also be refused by the `github-pages` environment. Instead, `release-windows.yml` ends with `gh workflow run pages.yml --ref master` (permission `actions: write`). The spec has been updated to match.

## Review Focus

1. **Old anchors keep working.** Anchors from the guide's hand-written table of contents and cross-references (for example `#use-sgpg-agentssh`, `#updating-keys`) must still resolve on the guide page. *Test: Task 3 strict build with `validation.anchors: warn`.*
2. **Code blocks are left alone.** A `](...)` or `> [!NOTE]` inside a fenced code block must not be rewritten. *Test: Task 1 `test_code_blocks_are_untouched`.*
3. **Alerts keep their content.** A multi-line alert containing an empty `>` line and inline code must keep every line inside the admonition. *Test: Task 1 `test_multiline_alert`.*
4. **Release notes stay in their own section.** A release body with `#`/`##` headings must not create top-level sections or break the page outline. *Test: Task 2 `test_notes_headings_are_demoted`.*
5. **No releases.** With no releases, or only drafts, the download page must still build and point to GitHub. *Test: Task 2 `test_no_published_release`.*

---

## File Structure

| File | Responsibility | Task |
| - | - | - |
| `site/build.py` (new) | Copies and adapts sources, writes `download.md` | 1, 2 |
| `site/test_build.py` (new) | Unit tests for build.py | 1, 2 |
| `site/home.md` (new) | Home page text | 3 |
| `site/mkdocs.yml` (new) | MkDocs Material config | 3 |
| `site/requirements.txt` (new) | `mkdocs-material==9.7.6`, `mkdocs==1.6.1` | 3 |
| `.github/workflows/pages.yml` (new) | Build and deploy to Pages | 4 |
| `.github/workflows/lint.yml` | Site unit tests and strict build | 4 |
| `.github/workflows/release-windows.yml` | Trigger pages.yml after a release | 4 |
| `.markdownlint-cli2.jsonc` | Add `site/home.md` to globs | 3 |
| `CLAUDE.md` | "Docs site" note | 4 |

---

### Task 1: Build script, part 1: copying, ToC, links and alerts

**Files:**

- Create: `site/build.py`
- Create: `site/test_build.py`

**Interfaces:**

- Produces in `site/build.py`:
  - constants `ROOT`, `SITE`, `REPO_URL`, `PAGES: dict[str, str]` (source path → page file), `TITLES: dict[str, str]`, `ALERTS: dict[str, tuple[str, str]]`;
  - `strip_toc(text: str) -> str`;
  - `rewrite_link(target: str, source: str) -> str`;
  - `convert_alerts(text: str) -> str`;
  - `adapt(text: str, source: str) -> str`, which applies link rewriting and alert conversion outside fenced code, plus ToC stripping and front matter for sources in `TITLES`;
  - `build(out: Path, releases: list | None) -> None`;
  - `main(argv: list[str] | None = None) -> int`.
  - Task 2 adds `render_download(releases: list | None) -> str`; until then, `build` writes a one-line placeholder `download.md`.

- [ ] **Step 1: Write the failing tests** (`site/test_build.py`)

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

UP = "https://github.com/drduh/YubiKey-Guide"


class TocTests(unittest.TestCase):
    def test_strips_list_up_to_rule(self):
        text = "Intro.\n\n- [A](#a)\n  - [B](#b)\n\n---\n\n# A\n\nBody\n"
        self.assertEqual(build.strip_toc(text), "Intro.\n\n# A\n\nBody\n")

    def test_without_toc_is_unchanged(self):
        self.assertEqual(build.strip_toc("# A\n\nBody\n"), "# A\n\nBody\n")


class LinkTests(unittest.TestCase):
    def test_relative_link_between_sources(self):
        self.assertEqual(build.rewrite_link("recovery/README.md", "README.md"), "recovery.md")
        self.assertEqual(build.rewrite_link("../README.md#x", "piv/README.md"), "guide.md#x")

    def test_upstream_blob_links_to_sources(self):
        for branch in ("main", "master"):
            self.assertEqual(build.rewrite_link(f"{UP}/blob/{branch}/SECENV.md#y", "README.md"), "secenv.md#y")

    def test_upstream_readme_tab(self):
        self.assertEqual(build.rewrite_link(f"{UP}?tab=readme-ov-file#identity", "SECENV.md"), "guide.md#identity")

    def test_relative_link_to_other_repo_file(self):
        self.assertEqual(build.rewrite_link("./yk.cnf", "piv/README.md"),
                         "https://github.com/mezinster/YubiKey-Guide/blob/master/piv/yk.cnf")

    def test_media_stays_relative(self):
        self.assertEqual(build.rewrite_link("media/schema_gpg.png", "README.md"), "media/schema_gpg.png")

    def test_unchanged_links(self):
        for target in ("#anchor", f"{UP}/blob/main/config/gpg.conf", f"{UP}/issues/445", UP,
                       "https://www.yubico.com/genuine/", "mailto:a@example.org"):
            self.assertEqual(build.rewrite_link(target, "README.md"), target)


class AlertTests(unittest.TestCase):
    def test_single_line_alert(self):
        text = "Before\n\n> [!WARNING]\n> Erases the device!\n\nAfter\n"
        self.assertEqual(build.convert_alerts(text),
                         'Before\n\n!!! warning "Warning"\n    Erases the device!\n\nAfter\n')

    def test_multiline_alert(self):
        text = "> [!TIP]\n> Use quotes:\n>\n> `export X='a \"b\"'`\n"
        self.assertEqual(build.convert_alerts(text),
                         '!!! tip "Tip"\n    Use quotes:\n\n    `export X=\'a "b"\'`\n')

    def test_every_alert_type(self):
        for kind, (admonition, title) in build.ALERTS.items():
            out = build.convert_alerts(f"> [!{kind}]\n> x\n")
            self.assertEqual(out, f'!!! {admonition} "{title}"\n    x\n')

    def test_plain_quote_is_untouched(self):
        self.assertEqual(build.convert_alerts("> just a quote\n"), "> just a quote\n")


class AdaptTests(unittest.TestCase):
    def test_code_blocks_are_untouched(self):
        fence = "`" * 3  # spelled out so this file can sit inside a Markdown code block
        text = f"[r](recovery/README.md)\n\n{fence}console\n$ echo '[r](recovery/README.md)'\n> [!NOTE]\n{fence}\n"
        out = build.adapt(text, "SECENV.md")
        self.assertIn("[r](recovery.md)\n", out)
        self.assertIn(f"$ echo '[r](recovery/README.md)'\n> [!NOTE]\n{fence}", out)
        self.assertEqual(out.count("\n"), text.count("\n"))

    def test_guide_gets_title_and_loses_toc(self):
        out = build.adapt("Intro.\n\n- [A](#a)\n\n---\n\n# A\n", "README.md")
        self.assertTrue(out.startswith("---\ntitle: Guide\n---\n\nIntro.\n"))
        self.assertNotIn("- [A](#a)", out)


class BuildTests(unittest.TestCase):
    def test_build_from_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b"
            build.build(out, None)
            self.assertEqual(sorted(p.name for p in out.glob("*.md")),
                             ["download.md", "guide.md", "index.md", "piv.md", "recovery.md", "secenv.md"])
            self.assertTrue((out / "media" / "schema_gpg.png").is_file())
            guide = (out / "guide.md").read_text(encoding="utf-8")
            self.assertIn("](recovery.md)", guide)
            self.assertNotIn("> [!", guide)
            self.assertNotIn("- [Purchase YubiKey](#purchase-yubikey)", guide)
            self.assertIn("# Purchase YubiKey", guide)

    def test_sources_are_not_modified(self):
        before = (build.ROOT / "README.md").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            build.build(Path(tmp) / "b", None)
        self.assertEqual((build.ROOT / "README.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
```

`BuildTests` needs `site/home.md`, which Task 3 creates. In this task, create a placeholder `site/home.md` containing `# YubiKey Guide\n`; Task 3 replaces it.

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 -m unittest discover -s site -v`
Expected: `ModuleNotFoundError: No module named 'build'`.

- [ ] **Step 3: Write `site/build.py`**

```python
#!/usr/bin/env python3
"""Prepares the MkDocs source folder site/.build from the repo's Markdown files.

The sources are only read; links, GitHub alerts and the guide's hand-written table of contents are
adapted in the copies. See docs/superpowers/specs/2026-10-12-github-pages-site-design.md.
"""
import argparse
import json
import posixpath
import re
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
REPO_URL = "https://github.com/mezinster/YubiKey-Guide"
PAGES = {  # source, relative to the repo root -> page file in the build folder
    "site/home.md": "index.md",
    "README.md": "guide.md",
    "SECENV.md": "secenv.md",
    "piv/README.md": "piv.md",
    "recovery/README.md": "recovery.md",
}
TITLES = {"README.md": "Guide"}  # pages whose source has no leading H1 to take the title from
ALERTS = {  # GitHub alert -> Material admonition type and title
    "NOTE": ("note", "Note"),
    "TIP": ("tip", "Tip"),
    "IMPORTANT": ("info", "Important"),
    "WARNING": ("warning", "Warning"),
    "CAUTION": ("danger", "Caution"),
}
GITHUB_REPO = re.compile(
    r"^https://github\.com/(?:drduh|mezinster)/YubiKey-Guide"
    r"(?:/blob/(?:main|master)/(?P<path>[^?#]+))?(?P<query>\?[^#]*)?(?P<anchor>#.*)?$")
LINK = re.compile(r"\]\((?P<target>[^)\s]+)(?P<title>\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")  # a fenced code block opens or closes


def strip_toc(text: str) -> str:
    """Removes the hand-written table of contents: the first "- [" list up to the next "---" line."""
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("- [")), None)
    if start is None:
        return text
    end = next((i for i in range(start, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    while end + 1 < len(lines) and not lines[end + 1].strip():
        end += 1
    return "\n".join(lines[:start] + lines[end + 1:])


def rewrite_link(target: str, source: str) -> str:
    """Points a link from `source` at the site page it names, or at GitHub for other repo files."""
    if target.startswith(("#", "mailto:")):
        return target
    match = GITHUB_REPO.match(target)
    if match:
        path, query, anchor = match["path"], match["query"] or "", match["anchor"] or ""
        if path in PAGES:
            return PAGES[path] + anchor
        if path is None and "tab=readme-ov-file" in query:
            return PAGES["README.md"] + anchor
        return target
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return target  # another site
    path, _, anchor = target.partition("#")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    anchor = "#" + anchor if anchor else ""
    if resolved in PAGES:
        return PAGES[resolved] + anchor
    if resolved.startswith("media/"):
        return resolved + anchor
    return f"{REPO_URL}/blob/master/{resolved}{anchor}"


def convert_alerts(text: str) -> str:
    """Turns GitHub alert blocks ("> [!NOTE]" ...) into Material admonitions ("!!! note")."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        match = re.match(r"^> \[!(\w+)\]\s*$", lines[i])
        if not match or match[1] not in ALERTS:
            out.append(lines[i])
            i += 1
            continue
        kind, title = ALERTS[match[1]]
        out.append(f'!!! {kind} "{title}"')
        i += 1
        while i < len(lines) and lines[i].startswith(">"):
            body = lines[i][1:]
            body = body[1:] if body.startswith(" ") else body
            out.append("    " + body if body.strip() else "")
            i += 1
    return "\n".join(out)


def _outside_code(text: str, change) -> str:
    """Applies `change` to the parts of `text` that are not in fenced code blocks."""
    parts, chunk, fence = [], [], None
    for line in text.split("\n"):
        opener = FENCE.match(line)
        if fence is None and opener:
            parts.append(change("\n".join(chunk)))
            chunk, fence = [line], opener[1]
        elif fence is not None and line.strip().startswith(fence):
            chunk.append(line)
            parts.append("\n".join(chunk))
            chunk, fence = [], None
        else:
            chunk.append(line)
    parts.append(change("\n".join(chunk)) if fence is None else "\n".join(chunk))
    return "\n".join(parts)


def adapt(text: str, source: str) -> str:
    def change(part: str) -> str:
        part = LINK.sub(lambda m: f"]({rewrite_link(m['target'], source)}{m['title'] or ''})", part)
        return convert_alerts(part)

    if source in TITLES:
        text = f"---\ntitle: {TITLES[source]}\n---\n\n" + strip_toc(text)
    return _outside_code(text, change)


def build(out: Path, releases) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source, page in PAGES.items():
        text = (ROOT / source).read_text(encoding="utf-8")
        (out / page).write_text(adapt(text, source), encoding="utf-8")
    shutil.copytree(ROOT / "media", out / "media")
    (out / "download.md").write_text("# Download\n", encoding="utf-8")  # Task 2: render_download


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releases", type=Path, help="JSON from `gh api repos/OWNER/REPO/releases`")
    parser.add_argument("--out", type=Path, default=SITE / ".build")
    args = parser.parse_args(argv)
    releases = json.loads(args.releases.read_text(encoding="utf-8")) if args.releases else None
    build(args.out, releases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s site -v`
Expected: all tests in `TocTests`, `LinkTests`, `AlertTests`, `AdaptTests` and `BuildTests` pass.

- [ ] **Step 5: Spot-check the real guide**

Run: `python3 site/build.py && grep -c '^!!! ' site/.build/*.md && git status --short`

Expected:

- 29 admonitions in total across the pages; README holds most of them.
- `git status` shows only the new `site/` files, and no source file modified.

- [ ] **Step 6: Commit**

```bash
git add site/build.py site/test_build.py site/home.md
git commit -m "docs: build script that adapts the guide for a pages site"
```

---

### Task 2: Build script, part 2: download page

**Files:**

- Modify: `site/build.py` (add `render_download`, use it in `build`)
- Modify: `site/test_build.py`

**Interfaces:**

- Consumes: `build(out, releases)` and `REPO_URL` (Task 1).
- Produces: `render_download(releases: list | None) -> str`.
  - `releases` is the parsed JSON list from the GitHub REST "list releases" endpoint. The fields used are `tag_name`, `name`, `draft`, `prerelease`, `published_at`, `html_url`, `body` and `assets[].name`, `.size` and `.browser_download_url`.

- [ ] **Step 1: Write the failing tests** (append to `site/test_build.py`, before `if __name__`)

```python
def release(tag, date, pre=False, draft=False, body="", assets=None):
    return {"tag_name": tag, "name": tag, "draft": draft, "prerelease": pre,
            "published_at": f"{date}T10:00:00Z", "html_url": f"https://github.com/x/r/releases/tag/{tag}",
            "body": body, "assets": assets if assets is not None else [
                {"name": "ykrecover-windows-x64.zip", "size": 30165643,
                 "browser_download_url": f"https://github.com/x/r/releases/download/{tag}/ykrecover-windows-x64.zip"}]}


class DownloadTests(unittest.TestCase):
    def test_latest_first_with_zip_and_size(self):
        page = build.render_download([release("v0.1.2-beta", "2026-10-11", pre=True),
                                      release("v0.1.3-beta", "2026-10-12", pre=True)])
        self.assertLess(page.index("## v0.1.3-beta"), page.index("v0.1.2-beta"))
        self.assertIn("## v0.1.3-beta (pre-release)", page)
        self.assertIn("[ykrecover-windows-x64.zip](https://github.com/x/r/releases/download/v0.1.3-beta/"
                      "ykrecover-windows-x64.zip) (30.2 MB)", page)
        self.assertIn("Published 2026-10-12.", page)
        self.assertIn("## Earlier releases", page)
        self.assertIn("| [v0.1.2-beta](https://github.com/x/r/releases/tag/v0.1.2-beta) | 2026-10-11 |", page)

    def test_drafts_are_skipped(self):
        page = build.render_download([release("v9", "2026-12-01", draft=True), release("v1", "2026-10-01")])
        self.assertNotIn("v9", page)
        self.assertIn("## v1\n", page)
        self.assertNotIn("Earlier releases", page)

    def test_notes_headings_are_demoted(self):
        page = build.render_download([release("v1", "2026-10-01", body="# Big\n\nText\n\n## Small")])
        self.assertIn("### Release notes", page)
        self.assertIn("#### Big", page)
        self.assertIn("##### Small", page)
        self.assertNotIn("\n# Big", page)

    def test_no_published_release(self):
        for releases in (None, [], [release("v9", "2026-12-01", draft=True)]):
            page = build.render_download(releases)
            self.assertIn("# Download", page)
            self.assertIn("https://github.com/mezinster/YubiKey-Guide/releases", page)

    def test_build_writes_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b"
            build.build(out, [release("v1", "2026-10-01")])
            self.assertIn("## v1", (out / "download.md").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 -m unittest discover -s site -v`
Expected: `DownloadTests` errors with `AttributeError: module 'build' has no attribute 'render_download'`.

- [ ] **Step 3: Implement** (add to `site/build.py` after `adapt`, and replace the placeholder line in `build`)

```python
def _demote(notes: str) -> str:
    """Puts any heading of the release notes below the page's "### Release notes" heading."""
    return re.sub(r"^(#{1,6}) ", lambda m: "#" * min(6, len(m[1]) + 3) + " ", notes, flags=re.M)


def render_download(releases) -> str:
    lines = ["# Download", "",
             "Windows builds of the ykrecover recovery tool. Unpack the zip and run `ykrecover.exe`; "
             "see [Recovery tool](recovery.md) for what it needs and how to use it.", ""]
    published = sorted((r for r in releases or () if not r.get("draft")),
                       key=lambda r: r.get("published_at") or "", reverse=True)
    if not published:
        lines.append(f"Releases are listed on [GitHub]({REPO_URL}/releases).")
        return "\n".join(lines) + "\n"
    latest = published[0]
    name = latest.get("name") or latest["tag_name"]
    lines += [f"## {name}" + (" (pre-release)" if latest.get("prerelease") else ""), "",
              f"Published {(latest.get('published_at') or '')[:10]}.", ""]
    for asset in latest.get("assets") or ():
        lines.append(f"- [{asset['name']}]({asset['browser_download_url']}) "
                     f"({asset['size'] / 1_000_000:.1f} MB)")
    notes = (latest.get("body") or "").strip()
    if notes:
        lines += ["", "### Release notes", "", _demote(notes)]
    if len(published) > 1:
        lines += ["", "## Earlier releases", "", "| Release | Date | |", "| - | - | - |"]
        for r in published[1:]:
            label = "pre-release" if r.get("prerelease") else ""
            lines.append(f"| [{r.get('name') or r['tag_name']}]({r['html_url']}) | "
                         f"{(r.get('published_at') or '')[:10]} | {label} |")
    lines += ["", f"All releases: [GitHub]({REPO_URL}/releases)."]
    return "\n".join(lines) + "\n"
```

In `build`, replace the placeholder line with:

```python
    (out / "download.md").write_text(render_download(releases), encoding="utf-8")
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s site -v`
Expected: all tests pass.

- [ ] **Step 5: Check against the real release list**

Run: `gh api repos/mezinster/YubiKey-Guide/releases > /tmp/claude-1000/releases.json && python3 site/build.py --releases /tmp/claude-1000/releases.json && cat site/.build/download.md`

Expected: `## v0.1.3-beta (pre-release)` comes first, with the zip link (about 30 MB), followed by an "Earlier releases" table listing v0.1.2-beta, v0.1.1-beta and v0.1.0-beta.

- [ ] **Step 6: Commit**

```bash
git add site/build.py site/test_build.py
git commit -m "docs: generate a download page from the github releases"
```

---

### Task 3: MkDocs config, home page and a strict local build

**Files:**

- Create: `site/mkdocs.yml`
- Create: `site/requirements.txt`
- Modify: `site/home.md` (replaces the placeholder from Task 1)
- Modify: `.markdownlint-cli2.jsonc` (add `site/home.md` to `globs`)

**Interfaces:**

- Consumes: `python3 site/build.py [--releases FILE]` writes `site/.build/` (Tasks 1 and 2).
- Produces: `mkdocs build --strict -f site/mkdocs.yml` writes `site/.site/`. Task 4 uploads that folder.

- [ ] **Step 1: `site/requirements.txt`**

```text
mkdocs-material==9.7.6
mkdocs==1.6.1  # Material 9 does not support MkDocs 2.0
```

- [ ] **Step 2: `site/mkdocs.yml`**

```yaml
site_name: YubiKey Guide
site_url: https://mezinster.github.io/YubiKey-Guide/
site_description: Using a YubiKey with GnuPG for encryption, signing and SSH, and recovering it from a backup.
repo_url: https://github.com/mezinster/YubiKey-Guide
repo_name: mezinster/YubiKey-Guide
edit_uri: ""
docs_dir: .build  # written by build.py from the repo's Markdown files; never edit it
site_dir: .site
strict: true

theme:
  name: material
  features:
    - navigation.sections
    - navigation.top
    - toc.follow
    - search.suggest
    - search.highlight
    - content.code.copy
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      toggle:
        icon: material/brightness-4
        name: Switch to light mode

markdown_extensions:
  - admonition
  - attr_list
  - md_in_html
  - pymdownx.details
  - pymdownx.superfences
  - toc:
      permalink: true
      slugify: !!python/object/apply:pymdownx.slugs.slugify
        kwds:
          case: lower

validation:
  links:
    not_found: warn
    anchors: warn
    absolute_links: warn
    unrecognized_links: warn

nav:
  - Home: index.md
  - Guide: guide.md
  - Secure environment: secenv.md
  - PIV: piv.md
  - Recovery tool: recovery.md
  - Download: download.md
```

- [ ] **Step 3: `site/home.md`**

```markdown
# YubiKey Guide

This site is the guide to using a [YubiKey](https://www.yubico.com/) with GnuPG for encryption, signing and SSH. It also documents **ykrecover**, a tool that loads backed-up keys onto new YubiKeys.

## The guide

- [Guide](guide.md): prepare an offline environment, create the keys, move them to YubiKeys, and use them for GnuPG and SSH.
- [Secure environment](secenv.md): build an air-gapped Alpine Linux system for key work.
- [PIV](piv.md): use the YubiKey's PIV application with certificates.

## The recovery tool

[ykrecover](recovery.md) reads an existing key backup, in the layout the guide produces or in most other layouts. It then copies, renews or rotates the subkeys onto one or more new YubiKeys. It runs on Linux (Python or bash) and on Windows, from a [download](download.md).

## Source

The guide is written by [drduh](https://github.com/drduh/YubiKey-Guide). This site is built from the fork [mezinster/YubiKey-Guide](https://github.com/mezinster/YubiKey-Guide), which adds the recovery tool. Pages are generated from the repository's Markdown files on every change.
```

- [ ] **Step 4: Add the home page to markdownlint**

In `.markdownlint-cli2.jsonc`, change the `globs` line to:

```jsonc
  "globs": ["README.md", "SECENV.md", "piv/README.md", "recovery/README.md", "site/home.md"]
```

- [ ] **Step 5: Build strictly**

Run:

```bash
python3 -m venv /tmp/claude-1000/mkd && /tmp/claude-1000/mkd/bin/pip install -q -r site/requirements.txt
python3 site/build.py && /tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.yml
```

Expected: `INFO - Documentation built in …` and exit 0. This was verified while planning: the strict build passes, and `#use-sgpg-agentssh` and `#updating-keys` exist as ids. Material prints a large notice about MkDocs 2.0, which is harmless with the pins.

If the strict build reports problems, fix them in `build.py` or the config, never in the sources:

- **Anchor warnings** (for example `#use-sgpg-agentssh` not found): compare the slug MkDocs generated (`grep -o 'id="[^"]*"' site/.site/guide/index.html`) with GitHub's. Adjust the `slugify` options, for example `case: lower` or `normalize`, until every anchor used in the guide resolves. Add a unit test for any `build.py` change.
- **Unrecognized relative links:** add the rule to `rewrite_link` with a test.

- [ ] **Step 6: Look at it**

Run: `/tmp/claude-1000/mkd/bin/mkdocs serve -f site/mkdocs.yml -a 127.0.0.1:8000`, open `http://127.0.0.1:8000/YubiKey-Guide/` (WSLg browser or `curl`), and check:

- **Guide:** the sidebar table of contents, the `#updating-keys` link in the guide, an admonition (the WARNING before `dd`), the "Additional identities" `<details>` block, the `schema_gpg.png` image, and code copy buttons.
- **Download page:** it lists the real releases, after building with `--releases` as in Task 2 Step 5.

If no browser is available, at least run `curl -s http://127.0.0.1:8000/YubiKey-Guide/guide/ | grep -c 'admonition'`. Report in the task report what was seen.

- [ ] **Step 7: Lint and commit**

Run: `source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && npx --yes markdownlint-cli2@0.23.3`
Expected: `0 error(s)`.

```bash
git add site/mkdocs.yml site/requirements.txt site/home.md .markdownlint-cli2.jsonc
git commit -m "docs: mkdocs material site config and home page"
```

---

### Task 4: Workflows and docs

**Files:**

- Create: `.github/workflows/pages.yml`
- Modify: `.github/workflows/lint.yml` (the `lint` job)
- Modify: `.github/workflows/release-windows.yml`
- Modify: `CLAUDE.md`

**Interfaces:**

- Consumes:
  - `python3 site/build.py --releases FILE` and `mkdocs build --strict -f site/mkdocs.yml` (output `site/.site`);
  - `python3 -m unittest discover -s site`.

- [ ] **Step 1: `.github/workflows/pages.yml`**

```yaml
name: pages

on:
  push:
    branches: [master]
    paths:
      - README.md
      - SECENV.md
      - piv/README.md
      - recovery/README.md
      - media/**
      - site/**
      - .github/workflows/pages.yml
  workflow_dispatch:  # the Actions tab button, and release-windows.yml after a release

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: install mkdocs material
        run: pip install -r site/requirements.txt

      - name: list releases
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh api "repos/${{ github.repository }}/releases" > releases.json

      - name: build
        run: |
          python3 site/build.py --releases releases.json
          mkdocs build --strict -f site/mkdocs.yml

      - uses: actions/configure-pages@v5

      - uses: actions/upload-pages-artifact@v3
        with:
          path: site/.site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Site checks in `lint.yml`**

Append to the `lint` job's steps, after `shellcheck`:

```yaml
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: docs site (unit tests and strict build)
        run: |
          python3 -m unittest discover -s site -v
          pip install -r site/requirements.txt
          python3 site/build.py
          mkdocs build --strict -f site/mkdocs.yml
```

- [ ] **Step 3: Rebuild the site after a release (`release-windows.yml`)**

Change `permissions` to:

```yaml
permissions:
  contents: write
  actions: write  # start pages.yml once the release is ready
```

and append this step to the `windows` job, after "attach to the release":

```yaml
      - name: refresh the docs site download page
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run pages.yml --ref master --repo "${{ github.repository }}"
```

- [ ] **Step 4: `CLAUDE.md`**

Under `## Build Commands`, append to the bash block:

```bash
# Docs site (GitHub Pages, MkDocs Material); generated from the Markdown files above, never edit site/.build
python3 -m unittest discover -s site -v
python3 site/build.py && mkdocs build --strict -f site/mkdocs.yml   # pip install -r site/requirements.txt first
```

Under `## Repo Workflow`, add a bullet:

```markdown
- `site/` builds the GitHub Pages site (`.github/workflows/pages.yml`, on pushes to master, manual runs and after each release). `site/build.py` copies README.md, SECENV.md, piv/README.md and recovery/README.md into `site/.build` and adapts links and alerts there; a new top-level doc must be added to `PAGES` in build.py and to `nav` in `site/mkdocs.yml`.
```

- [ ] **Step 5: Validate the workflows**

Run:

```bash
python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" .github/workflows/pages.yml .github/workflows/lint.yml .github/workflows/release-windows.yml
```

(`yaml` comes from the MkDocs venv: use `/tmp/claude-1000/mkd/bin/python`.)

Expected: no output, exit 0. Also rerun `python3 -m unittest discover -s site` and the markdownlint command.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/pages.yml .github/workflows/lint.yml .github/workflows/release-windows.yml CLAUDE.md
git commit -m "ci: build and deploy the docs site to github pages"
```

---

## After the tasks (controller, with the owner's approval)

These steps change repository settings outside the code, so ask the owner before running them:

1. Enable Pages with build type GitHub Actions: `gh api -X POST repos/mezinster/YubiKey-Guide/pages -f build_type=workflow`.
2. After the PR is merged, the push to master runs `pages.yml`. Check that the deploy job succeeded and that `https://mezinster.github.io/YubiKey-Guide/` serves the site.
3. Set the repo homepage: `gh repo edit mezinster/YubiKey-Guide --homepage https://mezinster.github.io/YubiKey-Guide/`.
