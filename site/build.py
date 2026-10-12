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
