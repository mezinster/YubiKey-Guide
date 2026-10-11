#!/usr/bin/env python3
"""Check that every heading in README.md is listed in its hand-written table of contents.

Broken anchor links are caught separately by markdownlint (MD051).
"""

import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "README.md"
lines = open(path, encoding="utf-8").read().splitlines()

# The ToC is the bullet list before the first `---` separator.
toc_end = lines.index("---")
toc = set(re.findall(r"\]\(#([^)]+)\)", "\n".join(lines[:toc_end])))


def slugify(text):
    """GitHub-style anchor: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", text).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


seen = {}
missing = []
in_fence = False
for number, line in enumerate(lines[toc_end:], start=toc_end + 1):
    if line.lstrip().startswith("```"):
        in_fence = not in_fence
        continue
    match = re.match(r"(#{1,6}) (.+)", line)
    if in_fence or not match:
        continue
    slug = slugify(match.group(2))
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    anchor = f"{slug}-{count}" if count else slug
    if anchor not in toc:
        missing.append(f"{path}:{number}: heading not in table of contents: {line} (#{anchor})")

print("\n".join(missing) or f"{path}: table of contents lists all headings")
sys.exit(1 if missing else 0)
