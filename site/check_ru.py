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
import build  # noqa: E402
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


def compare(en_build: Path, ru_build: Path, en_site: Path, ru_site: Path, pages=None,
            ru_dir: Path = build.RU) -> list:
    """Problems of current translations; an outdated one only warns and is not compared."""
    problems = []
    sources = {page: source for source, page in PAGES.items()}
    for page in pages or CHECKED:
        name = build.RU_FILES[sources[page]]
        if (ru_dir / name).exists():
            _, source_hash, _ = build.read_translation(ru_dir / name)
            if source_hash != build.blob_hash(build.ROOT / sources[page]):
                build.warn(f"ru/{name} is out of date with {sources[page]}; not compared", f"site/ru/{name}")
                continue
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
