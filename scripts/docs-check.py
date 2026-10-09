#!/usr/bin/env python3
"""Check the Markdown documentation: every relative link must point to an existing file, and
every #anchor to a heading of the target file (GitHub's anchor rules). External links
(http, mailto) are not fetched. Exit 1 when something is broken.

  scripts/docs-check.py            all *.md files in the repository (not in external/)"""

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SKIP_DIRS = {".git", "external", "node_modules", ".venv", ".deps", "data", "__pycache__"}
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def anchors(path, cache={}):
    """GitHub heading anchors of a Markdown file (plus explicit <a id=...>)."""
    if path not in cache:
        found, counts, fenced = set(), {}, False
        with open(path, encoding="utf-8") as f:
            for line in f:
                if FENCE.match(line):
                    fenced = not fenced
                    continue
                if fenced:
                    continue
                found.update(re.findall(r'<a\s+(?:id|name)="([^"]+)"', line))
                m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
                if m:
                    text = re.sub(r"`|\*\*|\*|_(?=\w)|(?<=\w)_|\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
                    slug = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
                    n = counts.get(slug, 0)
                    counts[slug] = n + 1
                    found.add(slug if n == 0 else f"{slug}-{n}")
        cache[path] = found
    return cache[path]


def markdown_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".md"):
                yield os.path.join(root, f)


def main():
    problems, checked = [], 0
    for md in sorted(markdown_files()):
        fenced = False
        with open(md, encoding="utf-8") as f:
            lines = f.readlines()
        for no, line in enumerate(lines, 1):
            if FENCE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue
            for target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target):      # http:, https:, mailto:
                    continue
                checked += 1
                path, _, anchor = target.partition("#")
                full = os.path.normpath(os.path.join(os.path.dirname(md), path)) if path else md
                where = f"{os.path.relpath(md, REPO)}:{no}"
                if not os.path.exists(full):
                    problems.append(f"{where}: missing file {target}")
                elif anchor and full.endswith(".md") and anchor not in anchors(full):
                    problems.append(f"{where}: no heading for #{anchor} in {os.path.relpath(full, REPO)}")
    for p in problems:
        print(p)
    print(f"{checked} link(s) checked, {len(problems)} broken")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
