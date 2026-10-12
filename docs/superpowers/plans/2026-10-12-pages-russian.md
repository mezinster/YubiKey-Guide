# Russian Docs Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Russian version of every page of `https://mezinster.github.io/YubiKey-Guide/` under `/ru/`, with a Russian interface, a language switcher, and an "outdated" banner when an English source changes.

**Architecture:** `site/build.py` gains a Russian pass: it reads `site/ru/*.md` translations (front matter `source`, `source_hash`), adapts them like the English sources, adds a banner when the English source's git blob hash changed, and writes `site/.build-ru/`. A second config, `site/mkdocs.ru.yml`, builds that into `site/.site/ru/`; `site/check_ru.py` compares the built Russian pages with the English ones (heading ids, code blocks). Translations are written by hand (Claude) following `site/ru/glossary.md`.

**Tech Stack:** Python 3.12 stdlib, MkDocs 1.6.1 + Material 9.7.6 (already pinned in `site/requirements.txt`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-12-pages-russian-design.md`

## Global Constraints

- English sources (`README.md`, `SECENV.md`, `piv/README.md`, `recovery/README.md`, `site/home.md`) are never modified.
- No new dependencies; `build.py`, `check_ru.py` and their tests are stdlib only.
- Translation files: `site/ru/home.md`, `guide.md`, `secenv.md`, `piv.md`, `recovery.md`, plus `site/ru/download.json` and `site/ru/glossary.md`. Each page starts with `source:` / `source_hash:` front matter; `source_hash` = `python3 site/build.py --hash <source>`.
- Russian headings carry the English anchor: `## Покупка YubiKey {#purchase-yubikey}`. Code blocks are byte-identical to the English ones (after dedent).
- Outdated translations warn (`warning:` / `::warning file=…::`), never fail the build. A missing/invalid header or wrong `source` fails it (exit 1).
- Build order: `python3 site/build.py`, then `mkdocs build --strict -f site/mkdocs.yml` (writes `site/.site`), then `mkdocs build --strict -f site/mkdocs.ru.yml` (writes `site/.site/ru`), then `python3 site/check_ru.py`. The English build cleans `site/.site`, so it must run first.
- Commit messages: short lowercase imperative, prefixed `docs:` or `ci:`. **No Co-Authored-By or other attribution lines.**
- Commands from the repo root. Unit tests: `python3 -m unittest discover -s site -v`. MkDocs: the venv at `/tmp/claude-1000/mkd` (create with `python3 -m venv /tmp/claude-1000/mkd && /tmp/claude-1000/mkd/bin/pip install -q -r site/requirements.txt` if missing). Markdown lint: `source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && npx --yes markdownlint-cli2@0.23.3`.

### Ruling made while planning (deviation from the spec text)

- The Russian build folder is `site/.build-ru/` (option `--out-ru`), not `site/.build/ru/`: MkDocs would otherwise treat the Russian pages as part of the English site (they would be built twice and indexed by English search). `mkdocs.ru.yml` uses `docs_dir: .build-ru`. The spec is updated to match.

## Review Focus

1. A Russian heading without `{#…}` gets a Cyrillic slug: links to that section break in Russian only. *Test: `check_ru.py` in Tasks 3–6 (heading ids).*
2. A translator "fixes" a command or translates a comment inside a code block: the Russian page would teach a different command. *Test: `check_ru.py` code-block comparison.*
3. The English build after the Russian one wipes `site/.site/ru`. *Handled by build order in Task 6's workflow steps; verified in Task 6 Step 4.*
4. An upstream sync changes `README.md`: Russian pages must still build and deploy, with a banner. *Test: Task 1 `test_outdated_translation_gets_banner_and_warning`; Task 6 Step 5 builds once with a stale hash.*
5. Images and cross-page links from `/ru/` pages must resolve inside `/ru/`. *Test: Task 1 `test_current_translation_has_no_banner` (media + link rewriting); strict Russian build.*

---

## File Structure

| File | Responsibility | Task |
| - | - | - |
| `site/build.py` | + Russian pass, blob hash, banner, Russian download strings, `--hash`, `--out-ru` | 1 |
| `site/test_build.py` | + `RussianTests` | 1 |
| `site/check_ru.py` (new) | Compare built Russian pages with English (heading ids, code blocks) | 2 |
| `site/test_check_ru.py` (new) | Tests for check_ru | 2 |
| `site/mkdocs.ru.yml` (new) | Russian MkDocs config | 3 |
| `site/mkdocs.yml` | + `extra.alternate` language switcher | 3 |
| `site/ru/glossary.md` (new) | Translation rules and glossary (not published) | 3 |
| `site/ru/download.json` (new) | Russian download page strings | 3 |
| `site/ru/home.md`, `site/ru/recovery.md` (new) | Translations | 3 |
| `site/ru/secenv.md`, `site/ru/piv.md` (new) | Translations | 4 |
| `site/ru/guide.md` (new) | Translation of README.md | 5 |
| `.github/workflows/pages.yml`, `.github/workflows/lint.yml`, `CLAUDE.md`, `.markdownlint-cli2.jsonc` | Build/check both languages; docs | 6 |

---

### Task 1: Russian pass in build.py

**Files:**

- Modify: `site/build.py` (replace with the content below)
- Modify: `site/test_build.py`

**Interfaces:**

- Produces in `site/build.py`: `SITE_URL`, `RU`, `RU_FILES: dict[str, str]` (source → translation file name), `RU_TITLES`, `RU_ALERTS`, `EN_STRINGS`; `class TranslationError(Exception)`; `blob_hash(path) -> str`; `read_translation(path) -> (source, source_hash, body)`; `stale_banner(page) -> str`; `insert_banner(text, banner) -> str`; `warn(message, path) -> None`; `build_ru(out: Path, releases, ru_dir: Path) -> None`; `adapt(text, source, titles=None, alerts=None)`; `convert_alerts(text, alerts=None)`; `render_download(releases, strings=None)`; CLI `--out-ru DIR` (default `site/.build-ru`) and `--hash FILE`.
- `build_ru` skips sources without a translation file and skips `download.md` without `download.json` (so Tasks 3–5 can add pages one by one).

- [ ] **Step 1: Add the failing tests**

In `site/test_build.py`, change the imports at the top to:

```python
import io
import re
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
```

and add this class before `if __name__ == "__main__":`:

```python
class RussianTests(unittest.TestCase):
    def translation(self, ru_dir, name, source, body, current=True):
        digest = build.blob_hash(build.ROOT / source) if current else "0" * 40
        (ru_dir / name).write_text(f"---\nsource: {source}\nsource_hash: {digest}\n---\n\n{body}",
                                   encoding="utf-8")

    def test_blob_hash_matches_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.txt"
            path.write_bytes(b"hello\n")
            self.assertEqual(build.blob_hash(path), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_current_translation_has_no_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            ru_dir, out = Path(tmp) / "ru", Path(tmp) / "out"
            ru_dir.mkdir()
            self.translation(ru_dir, "recovery.md", "recovery/README.md",
                             "# Восстановление\n\nСм. [руководство](../README.md#updating-keys).\n\n"
                             "> [!NOTE]\n> Текст\n\n![схема](media/schema_gpg.png)\n")
            build.build_ru(out, None, ru_dir)
            page = (out / "recovery.md").read_text(encoding="utf-8")
            self.assertNotIn("Перевод устарел", page)
            self.assertIn("](guide.md#updating-keys)", page)
            self.assertIn('!!! note "Примечание"', page)
            self.assertTrue((out / "media" / "schema_gpg.png").is_file())
            self.assertFalse((out / "download.md").exists())

    def test_outdated_translation_gets_banner_and_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            ru_dir, out = Path(tmp) / "ru", Path(tmp) / "out"
            ru_dir.mkdir()
            self.translation(ru_dir, "guide.md", "README.md", "# Покупка {#purchase-yubikey}\n\nТекст\n",
                             current=False)
            with mock.patch.object(build, "warn") as warn:
                build.build_ru(out, None, ru_dir)
            page = (out / "guide.md").read_text(encoding="utf-8")
            self.assertTrue(page.startswith("---\ntitle: Руководство\n---\n\n!!! warning \"Перевод устарел\""))
            self.assertIn("(https://mezinster.github.io/YubiKey-Guide/guide/)", page)
            self.assertIn("## Покупка {#purchase-yubikey}", page)
            warn.assert_called_once_with("ru/guide.md is out of date with README.md", "site/ru/guide.md")

    def test_banner_goes_after_the_first_h1(self):
        self.assertEqual(build.insert_banner("# Title\n\nBody\n", "BANNER\n"), "# Title\n\nBANNER\n\nBody\n")

    def test_missing_header_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ru_dir = Path(tmp) / "ru"
            ru_dir.mkdir()
            (ru_dir / "piv.md").write_text("# PIV\n", encoding="utf-8")
            with self.assertRaises(build.TranslationError):
                build.build_ru(Path(tmp) / "out", None, ru_dir)

    def test_wrong_source_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ru_dir = Path(tmp) / "ru"
            ru_dir.mkdir()
            self.translation(ru_dir, "piv.md", "SECENV.md", "# PIV\n")
            with self.assertRaises(build.TranslationError):
                build.build_ru(Path(tmp) / "out", None, ru_dir)

    def test_russian_download_page(self):
        strings = dict(build.EN_STRINGS, title="Загрузка", prerelease="предварительный выпуск",
                       none="Выпуски перечислены на [GitHub]({url}).")
        page = build.render_download([release("v1", "2026-10-01", pre=True)], strings)
        self.assertIn("# Загрузка", page)
        self.assertIn("## v1 (предварительный выпуск)", page)
        self.assertIn("Выпуски перечислены на [GitHub](https://github.com/mezinster/YubiKey-Guide/releases).",
                      build.render_download(None, strings))

    def test_english_download_page_is_unchanged(self):
        page = build.render_download([release("v1", "2026-10-01", pre=True)])
        self.assertIn("## v1 (pre-release)", page)

    def test_hash_option(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(build.main(["--hash", str(build.ROOT / "README.md")]), 0)
        self.assertEqual(out.getvalue().strip(), build.blob_hash(build.ROOT / "README.md"))
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 -m unittest discover -s site -v 2>&1 | tail -5`
Expected: `RussianTests` errors with `AttributeError: module 'build' has no attribute 'blob_hash'` (and similar); the earlier tests still pass.

- [ ] **Step 3: Replace `site/build.py` with:**

```python
#!/usr/bin/env python3
"""Prepares the MkDocs source folder site/.build from the repo's Markdown files.

The sources are only read; links, GitHub alerts and the guide's hand-written table of contents are
adapted in the copies. See docs/superpowers/specs/2026-10-12-github-pages-site-design.md.
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
REPO_URL = "https://github.com/mezinster/YubiKey-Guide"
SITE_URL = "https://mezinster.github.io/YubiKey-Guide/"
RU = SITE / "ru"  # Russian translations, one per source; see site/ru/glossary.md
PAGES = {  # source, relative to the repo root -> page file in the build folder
    "site/home.md": "index.md",
    "README.md": "guide.md",
    "SECENV.md": "secenv.md",
    "piv/README.md": "piv.md",
    "recovery/README.md": "recovery.md",
}
# Pages whose source has several H1 sections. Material's page sidebar lists only the first H1's
# children, so these get a title and every heading moves down a level.
TITLES = {"README.md": "Guide", "SECENV.md": "Secure environment", "piv/README.md": "PIV"}
STRIP_TOC = {"README.md"}  # sources with a hand-written table of contents
RU_FILES = {  # source -> its translation in site/ru
    "site/home.md": "home.md",
    "README.md": "guide.md",
    "SECENV.md": "secenv.md",
    "piv/README.md": "piv.md",
    "recovery/README.md": "recovery.md",
}
RU_TITLES = {"README.md": "Руководство", "SECENV.md": "Безопасная среда", "piv/README.md": "PIV"}
RU_ALERTS = {
    "NOTE": ("note", "Примечание"),
    "TIP": ("tip", "Совет"),
    "IMPORTANT": ("info", "Важно"),
    "WARNING": ("warning", "Внимание"),
    "CAUTION": ("danger", "Осторожно"),
}
EN_STRINGS = {  # the download page; site/ru/download.json has the same keys
    "title": "Download",
    "intro": "Windows builds of the ykrecover recovery tool. Unpack the zip and run `ykrecover.exe`; "
             "see [Recovery tool](recovery.md) for what it needs and how to use it.",
    "published": "Published {date}.",
    "notes": "Release notes",
    "earlier": "Earlier releases",
    "release": "Release",
    "date": "Date",
    "prerelease": "pre-release",
    "all": "All releases: [GitHub]({url}).",
    "none": "Releases are listed on [GitHub]({url}).",
}
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
    if posixpath.dirname(source) == "site" and resolved[len("site/"):] in (*PAGES.values(), "download.md"):
        return resolved[len("site/"):] + anchor  # site/home.md already names the site pages
    if resolved.startswith("media/"):
        return resolved + anchor
    return f"{REPO_URL}/blob/master/{resolved}{anchor}"


def convert_alerts(text: str, alerts=None) -> str:
    """Turns GitHub alert blocks ("> [!NOTE]" ...) into Material admonitions ("!!! note")."""
    alerts = alerts or ALERTS
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        match = re.match(r"^> \[!(\w+)\]\s*$", lines[i])
        if not match or match[1] not in alerts:
            out.append(lines[i])
            i += 1
            continue
        kind, title = alerts[match[1]]
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


LIST_ITEM = re.compile(r"^( *)([-*+]|\d+[.)])( +)\S")


def reindent_lists(text: str) -> str:
    """Gives nested list content the 4-space indents Python-Markdown needs; GitHub accepts 2 or 3."""
    out, stack, fence = [], [], None  # stack: (GitHub content indent, new content indent) per open item
    after_text = False  # the previous line is a non-blank line that is not a list item
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            after_text = False
            continue
        indent = len(line) - len(line.lstrip(" "))
        if fence is None:
            while stack and indent < stack[-1][0]:
                stack.pop()
        old_base, new_base = stack[-1] if stack else (0, 0)
        new_indent = new_base + max(0, indent - old_base) if stack else indent
        out.append(" " * new_indent + line[indent:])
        opener = FENCE.match(line)
        if fence is None and opener:
            fence = opener[1]
        elif fence is not None:
            if line.strip().startswith(fence):
                fence = None
        else:
            item = LIST_ITEM.match(line)
            if item:
                stack.append((indent + len(item[2]) + len(item[3]), new_indent + 4))
                if after_text:  # GitHub starts a list right after text; Python-Markdown needs a blank line
                    out.insert(len(out) - 1, "")
            after_text = not item
            continue
        after_text = False
    return "\n".join(out)


def demote_headings(text: str) -> str:
    """Moves every ATX heading outside fenced code down one level (# -> ##)."""
    return _outside_code(text, lambda part: re.sub(r"^(#{1,5}) ", r"#\1 ", part, flags=re.M))


def adapt(text: str, source: str, titles=None, alerts=None) -> str:
    """Adapts `text` (the source itself, or its translation) for the site page made from `source`."""
    titles = TITLES if titles is None else titles

    def change(part: str) -> str:
        part = LINK.sub(lambda m: f"]({rewrite_link(m['target'], source)}{m['title'] or ''})", part)
        return convert_alerts(part, alerts)

    if source in STRIP_TOC:
        text = strip_toc(text)
    if source in titles:
        text = f"---\ntitle: {titles[source]}\n---\n\n" + demote_headings(text)
    return _outside_code(reindent_lists(text), change)


def _demote(notes: str) -> str:
    """Puts any heading of the release notes below the page's "### Release notes" heading."""
    def change(part: str) -> str:
        part = re.sub(r"^(?!\s*$)(.+)\n=+[ \t]*$", r"# \1", part, flags=re.M)  # setext to ATX first
        part = re.sub(r"^(?!\s*$)(?![-*+] )(.+)\n-+[ \t]*$", r"## \1", part, flags=re.M)
        return re.sub(r"^(#{1,6}) ", lambda m: "#" * min(6, len(m[1]) + 3) + " ", part, flags=re.M)

    return _outside_code(notes, change)


def _plain(text: str) -> str:
    """Release text from GitHub, with HTML neutralised: MkDocs would pass raw HTML through."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_download(releases, strings=None) -> str:
    s = strings or EN_STRINGS
    lines = [f"# {s['title']}", "", s["intro"], ""]
    published = sorted((r for r in releases or () if not r.get("draft")),
                       key=lambda r: r.get("published_at") or "", reverse=True)
    if not published:
        lines.append(s["none"].format(url=f"{REPO_URL}/releases"))
        return "\n".join(lines) + "\n"
    latest = published[0]
    name = _plain(latest.get("name") or latest["tag_name"])
    lines += [f"## {name}" + (f" ({s['prerelease']})" if latest.get("prerelease") else ""), "",
              s["published"].format(date=(latest.get("published_at") or "")[:10]), ""]
    for asset in latest.get("assets") or ():
        lines.append(f"- [{_plain(asset['name'])}]({asset['browser_download_url']}) "
                     f"({asset['size'] / 1_000_000:.1f} MB)")
    notes = _plain((latest.get("body") or "").strip())
    if notes:
        lines += ["", f"### {s['notes']}", "", _demote(notes)]
    if len(published) > 1:
        lines += ["", f"## {s['earlier']}", "", f"| {s['release']} | {s['date']} | |", "| - | - | - |"]
        for r in published[1:]:
            label = s["prerelease"] if r.get("prerelease") else ""
            lines.append(f"| [{_plain(r.get('name') or r['tag_name'])}]({r['html_url']}) | "
                         f"{(r.get('published_at') or '')[:10]} | {label} |")
    lines += ["", s["all"].format(url=f"{REPO_URL}/releases")]
    return "\n".join(lines) + "\n"


class TranslationError(Exception):
    pass


def blob_hash(path: Path) -> str:
    """What `git hash-object` prints for the file: SHA-1 of "blob <size>\\0" and the content."""
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_translation(path: Path) -> tuple:
    """Returns (source, source_hash, body) from a translation's front matter."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n\n?", text, re.S)
    fields = dict(re.findall(r"^(\w+):\s*(\S+)\s*$", match[1], re.M)) if match else {}
    if not {"source", "source_hash"} <= fields.keys():
        raise TranslationError(f"{path}: needs front matter with source and source_hash")
    return fields["source"], fields["source_hash"], text[match.end():]


def stale_banner(page: str) -> str:
    url = SITE_URL + ("" if page == "index.md" else page[:-3] + "/")
    return ('!!! warning "Перевод устарел"\n'
            f"    Английский текст изменился после перевода. Актуальная версия — [на английском]({url}).\n")


def insert_banner(text: str, banner: str) -> str:
    """Puts `banner` after the page's front matter or first H1, so the title stays on top."""
    match = re.match(r"^---\n.*?\n---\n\n?", text, re.S) or re.match(r"^# .*\n\n?", text)
    at = match.end() if match else 0
    return text[:at] + banner + "\n" + text[at:]


def warn(message: str, path: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning file={path}::{message}", file=sys.stderr)
    else:
        print(f"warning: {message}", file=sys.stderr)


def build_ru(out: Path, releases, ru_dir: Path) -> None:
    """Builds the Russian pages that have a translation in `ru_dir`."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source, name in RU_FILES.items():
        path = ru_dir / name
        if not path.exists():
            continue
        recorded, recorded_hash, body = read_translation(path)
        if recorded != source:
            raise TranslationError(f"{path}: source is {recorded}, expected {source}")
        page = PAGES[source]
        text = adapt(body, source, RU_TITLES, RU_ALERTS)
        if recorded_hash != blob_hash(ROOT / source):
            text = insert_banner(text, stale_banner(page))
            warn(f"ru/{name} is out of date with {source}", f"site/ru/{name}")
        (out / page).write_text(text, encoding="utf-8")
    shutil.copytree(ROOT / "media", out / "media")
    strings = ru_dir / "download.json"
    if strings.exists():
        (out / "download.md").write_text(
            render_download(releases, json.loads(strings.read_text(encoding="utf-8"))), encoding="utf-8")


def build(out: Path, releases) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source, page in PAGES.items():
        text = (ROOT / source).read_text(encoding="utf-8")
        (out / page).write_text(adapt(text, source), encoding="utf-8")
    shutil.copytree(ROOT / "media", out / "media")
    (out / "download.md").write_text(render_download(releases), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releases", type=Path, help="JSON from `gh api repos/OWNER/REPO/releases`")
    parser.add_argument("--out", type=Path, default=SITE / ".build")
    parser.add_argument("--out-ru", type=Path, default=SITE / ".build-ru")
    parser.add_argument("--hash", type=Path, metavar="FILE", help="print FILE's git blob hash and exit")
    args = parser.parse_args(argv)
    if args.hash:
        print(blob_hash(args.hash))
        return 0
    releases = json.loads(args.releases.read_text(encoding="utf-8")) if args.releases else None
    build(args.out, releases)
    try:
        build_ru(args.out_ru, releases, RU)
    except TranslationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s site -v 2>&1 | tail -3`
Expected: `OK` (all earlier tests unchanged and passing — the English output is byte-identical).

Also: `python3 site/build.py && ls site/.build site/.build-ru` → English pages as before; `site/.build-ru` holds only `media` (no translations yet). `python3 site/build.py --hash README.md` equals `git hash-object README.md`.

- [ ] **Step 5: Commit**

```bash
git add site/build.py site/test_build.py
git commit -m "docs: russian pass in the site build with outdated-translation banners"
```

---

### Task 2: check_ru.py

**Files:**

- Create: `site/check_ru.py`
- Create: `site/test_check_ru.py`

**Interfaces:**

- Consumes: `build.FENCE`, `build.PAGES`.
- Produces: `heading_ids(html) -> list[str]` (ids of h2–h6), `code_blocks(markdown) -> list[str]` (dedented fenced blocks), `html_path(site, page) -> Path`, `compare(en_build, ru_build, en_site, ru_site, pages=None) -> list[str]`, CLI `python3 site/check_ru.py [page.md ...]` (exit 1 on problems; default checks every page in `PAGES`; `download.md` is never checked).

- [ ] **Step 1: Write `site/test_check_ru.py`**

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_ru  # noqa: E402

FENCE = "`" * 3


class CheckRuTests(unittest.TestCase):
    def test_heading_ids(self):
        html = '<h1 id="t">T</h1><h2 id="a">A</h2><h3 id="b-c">B</h3><p id="x">'
        self.assertEqual(check_ru.heading_ids(html), ["a", "b-c"])

    def test_code_blocks_are_dedented(self):
        md = f"Text\n\n    {FENCE}sh\n    gpg -K\n    {FENCE}\n\n{FENCE}\nls\n{FENCE}\n"
        self.assertEqual(check_ru.code_blocks(md), [f"{FENCE}sh\ngpg -K\n{FENCE}", f"{FENCE}\nls\n{FENCE}"])

    def site(self, root, lang_build, lang_site, page, md, html):
        (root / lang_build).mkdir(parents=True, exist_ok=True)
        (root / lang_build / page).write_text(md, encoding="utf-8")
        path = check_ru.html_path(root / lang_site, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    def compare_one(self, en_md, en_html, ru_md, ru_html):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.site(root, "b", "s", "piv.md", en_md, en_html)
            self.site(root, "br", "sr", "piv.md", ru_md, ru_html)
            return check_ru.compare(root / "b", root / "br", root / "s", root / "sr", ["piv.md"])

    def test_matching_pages_pass(self):
        md = f"## A\n\n{FENCE}\nls\n{FENCE}\n"
        self.assertEqual(self.compare_one(md, '<h2 id="a">', md.replace("## A", "## А {#a}"),
                                          '<h2 id="a">'), [])

    def test_missing_heading_and_changed_code_are_reported(self):
        problems = self.compare_one(f"## A\n\n{FENCE}\nls\n{FENCE}\n", '<h2 id="a"><h2 id="b">',
                                    f"## А\n\n{FENCE}\nls -l\n{FENCE}\n", '<h2 id="a">')
        self.assertEqual(problems, ["ru/piv.md: heading #b is missing",
                                    "ru/piv.md: code block 1 differs from the English one"])

    def test_missing_translation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "br").mkdir()
            self.assertEqual(check_ru.compare(root / "b", root / "br", root / "s", root / "sr", ["piv.md"]),
                             ["ru/piv.md: no translation"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and see it fail**

Run: `python3 -m unittest discover -s site 2>&1 | grep -E 'No module|Error' | head -2`
Expected: `ModuleNotFoundError: No module named 'check_ru'`.

- [ ] **Step 3: Write `site/check_ru.py`**

```python
#!/usr/bin/env python3
"""Checks the built Russian site against the English one.

Every heading id of an English page must exist on its Russian page, so links and anchors work in both
languages, and fenced code blocks must be identical: code, commands and file names are never translated.
"""
import re
import sys
import textwrap
from pathlib import Path

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE))
from build import FENCE, PAGES  # noqa: E402

HEADING_ID = re.compile(r'<h[2-6] id="([^"]+)"')
CHECKED = list(PAGES.values())  # the download page is generated, so it is not compared


def heading_ids(html: str) -> list:
    return HEADING_ID.findall(html)


def code_blocks(markdown: str) -> list:
    """The fenced code blocks of `markdown`, dedented, fences included."""
    blocks, block, fence = [], [], None
    for line in markdown.split("\n"):
        opener = FENCE.match(line)
        if fence is None and opener:
            block, fence = [line], opener[1]
        elif fence is not None:
            block.append(line)
            if line.strip().startswith(fence):
                blocks.append(textwrap.dedent("\n".join(block)))
                fence = None
    return blocks


def html_path(site: Path, page: str) -> Path:
    return site / ("index.html" if page == "index.md" else page[:-3] + "/index.html")


def compare(en_build: Path, ru_build: Path, en_site: Path, ru_site: Path, pages=None) -> list:
    problems = []
    for page in pages or CHECKED:
        if not (ru_build / page).exists():
            problems.append(f"ru/{page}: no translation")
            continue
        en_ids = heading_ids(html_path(en_site, page).read_text(encoding="utf-8"))
        ru_ids = set(heading_ids(html_path(ru_site, page).read_text(encoding="utf-8")))
        problems += [f"ru/{page}: heading #{i} is missing" for i in en_ids if i not in ru_ids]
        en_code = code_blocks((en_build / page).read_text(encoding="utf-8"))
        ru_code = code_blocks((ru_build / page).read_text(encoding="utf-8"))
        if len(en_code) != len(ru_code):
            problems.append(f"ru/{page}: {len(ru_code)} code blocks, the English page has {len(en_code)}")
        else:
            problems += [f"ru/{page}: code block {n} differs from the English one"
                         for n, (a, b) in enumerate(zip(en_code, ru_code), 1) if a != b]
    return problems


def main(argv=None) -> int:
    pages = (sys.argv[1:] if argv is None else argv) or None  # page files, e.g. guide.md; default all
    problems = compare(SITE / ".build", SITE / ".build-ru", SITE / ".site", SITE / ".site" / "ru", pages)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s site -v 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add site/check_ru.py site/test_check_ru.py
git commit -m "docs: check russian pages against english anchors and code"
```

---

### Task 3: Russian config, glossary, home, recovery and download pages

**Files:**

- Create: `site/mkdocs.ru.yml`, `site/ru/glossary.md`, `site/ru/download.json`, `site/ru/home.md`, `site/ru/recovery.md`
- Modify: `site/mkdocs.yml` (language switcher)

**Interfaces:**

- Consumes: `build.py` Russian pass (Task 1), `check_ru.py` (Task 2).
- Produces: a Russian site with `index.md`, `recovery.md`, `download.md`; Tasks 4–5 add the remaining pages.

- [ ] **Step 1: `site/mkdocs.ru.yml`**

```yaml
site_name: YubiKey Guide
site_url: https://mezinster.github.io/YubiKey-Guide/ru/
site_description: Using a YubiKey with GnuPG for encryption, signing and SSH, and recovering it from a backup.
repo_url: https://github.com/mezinster/YubiKey-Guide
repo_name: mezinster/YubiKey-Guide
edit_uri: ""
docs_dir: .build-ru  # written by build.py from site/ru; never edit it
site_dir: .site/ru
strict: true

theme:
  name: material
  language: ru
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
  - footnotes
  - md_in_html
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.tasklist:
      custom_checkbox: true
  - toc:
      permalink: true
      slugify: !!python/object/apply:pymdownx.slugs.slugify
        kwds:
          case: lower

plugins:
  - search:
      lang: [ru, en]

validation:
  links:
    not_found: warn
    anchors: warn
    absolute_links: warn
    unrecognized_links: warn

extra:
  alternate:
    - name: English
      link: /YubiKey-Guide/
      lang: en
    - name: Русский
      link: /YubiKey-Guide/ru/
      lang: ru

nav:
  - Главная: index.md
  - Руководство: guide.md
  - Безопасная среда: secenv.md
  - PIV: piv.md
  - Утилита восстановления: recovery.md
  - Загрузка: download.md
```

- [ ] **Step 2: Language switcher in `site/mkdocs.yml`** — insert before `nav:`:

```yaml
extra:
  alternate:
    - name: English
      link: /YubiKey-Guide/
      lang: en
    - name: Русский
      link: /YubiKey-Guide/ru/
      lang: ru

```

- [ ] **Step 3: `site/ru/glossary.md`** (exact content; it is the rulebook for Tasks 3–5)

````markdown
# Russian translation: rules and glossary

This file is not published. It records how the pages in `site/ru/` are translated, so that every
translation and every later update reads the same way.

## Header

Every page starts with front matter naming its English source and git's blob hash of that source at
translation time (`python3 site/build.py --hash <source>`):

```yaml
---
source: README.md
source_hash: <hash>
---
```

When the English source changes, the page shows an "outdated" banner until it is translated again and
the hash is updated.

## Never translated

- fenced code blocks (`site/check_ru.py` checks they are identical to the English ones) and inline code;
- commands, options, file and folder names, environment variables, key IDs, fingerprints, URLs;
- product names: YubiKey, GnuPG/GPG, OpenPGP, PIV, ykman, Gpg4win, Debian, Tails, macOS, Windows.

## Structure

- Same headings, in the same order and at the same level. Every heading ends with the English
  heading's anchor: `# Покупка YubiKey {#purchase-yubikey}`. The anchor is the GitHub slug of the
  English heading text: lower case, spaces to `-`, punctuation dropped (as in the English table of
  contents). `site/check_ru.py` checks that every English anchor exists.
- GitHub alerts stay alerts (`> [!NOTE]`, `> [!WARNING]` ...): only their text is translated.
- Lists, tables, images, `<details>`/`<summary>` blocks, footnotes and bold platform labels
  (**Linux**, **macOS** ...) keep their place. Link targets stay as they are; link text is translated.
- The guide's hand-written table of contents is left out: the site shows its own.

## Style

- Formal "вы"; short sentences; neutral technical Russian, no anglicisms where a common Russian term exists.
- Imperative for instructions: «Выполните», «Скопируйте», «Подключите».
- UI labels of programs stay in English, with a translation in parentheses on first use if helpful.

## Glossary

| English | Russian |
| - | - |
| Certify key | ключ сертификации (Certify) |
| Subkey | подключ |
| Signature / Encryption / Authentication subkey | подключ подписи / шифрования / аутентификации |
| passphrase | парольная фраза |
| PIN, Admin PIN, User PIN | PIN, PIN администратора, PIN пользователя |
| Reset Code | код сброса |
| air-gapped | изолированный от сети |
| live image / live environment | live-образ / live-среда |
| backup | резервная копия |
| encrypted volume | зашифрованный том |
| public key / private key | открытый ключ / закрытый ключ |
| revocation certificate | сертификат отзыва |
| expiration | срок действия |
| smart card | смарт-карта |
| touch policy | политика касания |
| keyring | связка ключей |
| fingerprint | отпечаток |
| key stub | заглушка ключа |
| identity (user ID) | идентификатор пользователя (user ID) |
| renew / rotate (subkeys) | продлить / заменить (подключи) |
| recovery tool | утилита восстановления |
| clone / renew / rotate (ykrecover modes) | clone / renew / rotate (режимы не переводятся) |
````

- [ ] **Step 4: `site/ru/download.json`**

```json
{
  "title": "Загрузка",
  "intro": "Сборки утилиты восстановления ykrecover для Windows. Распакуйте zip-архив и запустите `ykrecover.exe`; что для этого нужно и как ею пользоваться, описано на странице [Утилита восстановления](recovery.md).",
  "published": "Опубликован {date}.",
  "notes": "Примечания к выпуску",
  "earlier": "Предыдущие выпуски",
  "release": "Выпуск",
  "date": "Дата",
  "prerelease": "предварительный выпуск",
  "all": "Все выпуски: [GitHub]({url}).",
  "none": "Выпуски перечислены на [GitHub]({url})."
}
```

- [ ] **Step 5: Translate `site/home.md` → `site/ru/home.md` and `recovery/README.md` → `site/ru/recovery.md`**

Follow `site/ru/glossary.md` exactly. Header: `source: site/home.md` / `source: recovery/README.md` and `source_hash:` from `python3 site/build.py --hash <source>`. Every heading gets the English anchor, e.g. in recovery: `# Восстановление YubiKey {#yubikey-recovery}`; take each anchor from the English heading's GitHub slug (the ids in `site/.site/recovery/index.html` after an English build: `grep -o '<h[1-6] id="[^"]*"' site/.site/recovery/index.html`). Links inside `site/home.md` (`guide.md`, `secenv.md` …) stay as they are.

- [ ] **Step 6: Build and check**

Run:

```bash
python3 site/build.py
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.yml
/tmp/claude-1000/mkd/bin/mkdocs build --no-strict -f site/mkdocs.ru.yml 2>&1 | grep -E 'WARNING|built in'
python3 site/check_ru.py index.md recovery.md
```

Expected: the English build passes strict; the Russian build's only warnings are about the not-yet-translated `guide.md`, `secenv.md`, `piv.md` (nav entries and links to them); `check_ru.py` exits 0 with no output. No "out of date" warning from build.py.

- [ ] **Step 7: Lint and commit**

`npx markdownlint` (command in Global Constraints) must report 0 issues — `site/ru/*.md` are not in its globs yet (Task 6 adds them); lint them now with `npx --yes markdownlint-cli2@0.23.3 site/ru/home.md site/ru/recovery.md site/ru/glossary.md`.

```bash
git add site/mkdocs.ru.yml site/mkdocs.yml site/ru
git commit -m "docs: russian site config, glossary, home, recovery and download pages"
```

---

### Task 4: Translate SECENV.md and piv/README.md

**Files:**

- Create: `site/ru/secenv.md` (source `SECENV.md`), `site/ru/piv.md` (source `piv/README.md`)

- [ ] **Step 1: Translate** following `site/ru/glossary.md` (headers with `--hash`, English anchors on every heading, code blocks untouched, alerts kept as alerts).
- [ ] **Step 2: Build and check**

```bash
python3 site/build.py
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.yml
/tmp/claude-1000/mkd/bin/mkdocs build --no-strict -f site/mkdocs.ru.yml 2>&1 | grep -E 'WARNING|built in'
python3 site/check_ru.py index.md recovery.md secenv.md piv.md
```

Expected: Russian warnings only about `guide.md`; `check_ru.py` exits 0.

- [ ] **Step 3: Lint and commit**

```bash
npx --yes markdownlint-cli2@0.23.3 site/ru/secenv.md site/ru/piv.md
git add site/ru/secenv.md site/ru/piv.md
git commit -m "docs: russian translation of the secure environment and piv pages"
```

---

### Task 5: Translate README.md (the guide)

**Files:**

- Create: `site/ru/guide.md` (source `README.md`)

The guide has 2,300 lines; translate it in four parts written in order into the same file (header first):

1. intro paragraph, then `# Purchase YubiKey` … `# Create Certify key` (README.md lines 1–481; skip the hand-written table of contents, lines 3–64);
2. `# Create Subkeys` … `# Finish setup` (lines 482–1013);
3. `# Using YubiKey` (lines 1014–1943);
4. `# Updating keys` … end (lines 1944–2324, including the footnote definitions).

Lines such as `# Dump network state information` inside code blocks (around lines 2210–2232) are shell comments, not headings: they stay untranslated.

- [ ] **Step 1: Translate parts 1–4** following `site/ru/glossary.md`. After each part, run `python3 site/build.py` to catch header errors early.
- [ ] **Step 2: Build and check**

```bash
python3 site/build.py
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.yml
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.ru.yml
python3 site/check_ru.py
```

Expected: both strict builds pass; `check_ru.py` exits 0 (every English heading id present on every Russian page; all code blocks identical).

- [ ] **Step 3: Read it once on the built site** (`/tmp/claude-1000/mkd/bin/mkdocs serve -f site/mkdocs.ru.yml -a 127.0.0.1:8001`, or `grep`/`curl` the HTML): admonitions in Russian, `<details>` block, footnotes, the image, task list in «Завершение настройки».
- [ ] **Step 4: Lint and commit**

```bash
npx --yes markdownlint-cli2@0.23.3 site/ru/guide.md
git add site/ru/guide.md
git commit -m "docs: russian translation of the guide"
```

---

### Task 6: Workflows, lint and docs

**Files:**

- Modify: `.github/workflows/pages.yml`, `.github/workflows/lint.yml`, `.markdownlint-cli2.jsonc`, `CLAUDE.md`

- [ ] **Step 1: `pages.yml`** — replace the `build` step's `run` with:

```yaml
        run: |
          python3 site/build.py --releases releases.json
          mkdocs build --strict -f site/mkdocs.yml
          mkdocs build --strict -f site/mkdocs.ru.yml
          python3 site/check_ru.py
```

- [ ] **Step 2: `lint.yml`** — replace the `docs site (unit tests and strict build)` step's `run` with:

```yaml
        run: |
          python3 -m unittest discover -s site -v
          pip install -r site/requirements.txt
          python3 site/build.py
          mkdocs build --strict -f site/mkdocs.yml
          mkdocs build --strict -f site/mkdocs.ru.yml
          python3 site/check_ru.py
```

- [ ] **Step 3: `.markdownlint-cli2.jsonc`** — add `"site/ru/*.md"` to `globs`. **`CLAUDE.md`** — in the docs-site bash block, add `mkdocs build --strict -f site/mkdocs.ru.yml && python3 site/check_ru.py` after the English build line; in the `site/` bullet under "Repo Workflow", append: «Russian translations live in `site/ru/` (rules: `site/ru/glossary.md`); after an English doc changes, its Russian page shows an outdated banner until it is re-translated and its `source_hash` updated with `python3 site/build.py --hash <source>`.»

- [ ] **Step 4: Full local run in the workflow's order**

```bash
python3 -m unittest discover -s site
python3 site/build.py
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.yml
/tmp/claude-1000/mkd/bin/mkdocs build --strict -f site/mkdocs.ru.yml
python3 site/check_ru.py && ls site/.site/ru/index.html && grep -c 'Русский' site/.site/index.html
```

Expected: all pass; `site/.site/ru/index.html` exists; the English page has the switcher.

- [ ] **Step 5: Outdated banner end to end** — copy the repo to a temp dir (`cp -r` of README.md, SECENV.md, media, piv, recovery, site into `/tmp/claude-1000/ru-stale/`), append a line to its `README.md`, run its `python3 site/build.py` and both mkdocs builds there. Expected: `warning: ru/guide.md is out of date with README.md` on stderr, both strict builds still pass, and `site/.site/ru/guide/index.html` contains «Перевод устарел». Do not change the real repo.

- [ ] **Step 6: Lint and commit**

```bash
source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && npx --yes markdownlint-cli2@0.23.3
git add .github/workflows/pages.yml .github/workflows/lint.yml .markdownlint-cli2.jsonc CLAUDE.md
git commit -m "ci: build and check the russian site"
```
