#!/usr/bin/env python3
"""Compile the repository's Markdown documentation into one PDF, docs/vnap-secure-documentation.pdf.

  scripts/docs/build-pdf.py            build the PDF (needs scripts/docs/requirements.txt: make docs-pdf)
  scripts/docs/build-pdf.py --check    exit 1 if the PDF is missing or older than its sources (stdlib only;
                                       part of make test)

The PDF has a cover, a table of contents, the introduction in scripts/docs/pdf-intro.md, and every
tracked Markdown file outside external/, in the parts listed in PARTS; a file in no part is an error.
Its metadata carries a SHA-256 digest of all sources (Markdown, introduction, images, this script), which
--check compares. The build is deterministic: the same sources give the same bytes."""

import hashlib
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
HERE = os.path.dirname(os.path.realpath(__file__))
OUT = os.path.join(REPO, "docs", "vnap-secure-documentation.pdf")
INTRO_FILE = os.path.join(HERE, "pdf-intro.md")
GITHUB = "https://github.com/jodyhuntatx/vnap-secure/blob/main/"
TITLE = "VNAP-Secure Documentation"

# ------------------------------------------------------------------ the documents
PARTS = [
    ("Overview", ["README.md", "docs/architecture.md"]),
    ("Installation and build", ["docs/installation.md", "docs/build.md", "docs/certificates.md", "certs/README.md"]),
    ("How it works", ["docs/functional/security-and-chain-verification.md", "docs/functional/pseudonym-change.md",
                      "docs/functional/certificate-refill.md", "docs/functional/vehicle-movement.md",
                      "docs/functional/eavesdropper-and-scoring.md"]),
    ("Operations", ["docs/operations/vnapctl.md", "docs/operations/service-deployment.md", "docs/operations/web-ui.md",
                    "docs/operations/backup-restore.md", "docs/operations/monitoring.md",
                    "docs/operations/troubleshooting.md"]),
    ("Reference", ["docs/reference/scenario-format.md", "docs/reference/configuration.md",
                   "docs/reference/container-environment.md", "docs/reference/control-channels.md",
                   "docs/reference/api.md", "sim/scenarios/README.md"]),
    ("Patch set", ["docs/patches/README.md", "docs/patches/build-and-container.md",
                   "docs/patches/certificate-verification.md", "docs/patches/thread-safety.md",
                   "docs/patches/pseudonym-and-id-change.md", "docs/patches/certificate-refill.md",
                   "docs/patches/mobility-and-cam.md"]),
    ("Results", ["results/README.md"]),
    ("Appendix", ["CHANGELOG.md", "sim/images/eavesdropper/asn1/README.md"]),
]
INTRO = "INTRO"
DOCS = [d for _, ds in PARTS for d in ds]
IMAGES = "docs/images"


def check_parts():
    """Every tracked Markdown file outside the submodule (and the introduction) must be in a part."""
    tracked = subprocess.run(["git", "-C", REPO, "ls-files", "*.md"], capture_output=True, text=True,
                             check=True).stdout.split()
    intro = os.path.relpath(INTRO_FILE, REPO)
    missing = sorted(f for f in tracked if not f.startswith("external/") and f != intro and f not in DOCS)
    gone = sorted(d for d in DOCS if not os.path.exists(os.path.join(REPO, d)))
    if missing or gone:
        sys.exit("scripts/docs/build-pdf.py: update PARTS: "
                 + "; ".join(x for x in (f"not in any part: {', '.join(missing)}" if missing else "",
                                         f"no longer exist: {', '.join(gone)}" if gone else "") if x))


def digest():
    """SHA-256 over everything the PDF is made from."""
    h = hashlib.sha256()
    files = [os.path.realpath(__file__), INTRO_FILE] + [os.path.join(REPO, d) for d in DOCS]
    img_dir = os.path.join(REPO, IMAGES)
    if os.path.isdir(img_dir):
        files += [os.path.join(img_dir, f) for f in sorted(os.listdir(img_dir))]
    for f in files:
        h.update(os.path.relpath(f, REPO).encode() + b"\0")
        with open(f, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


check_parts()
DIGEST = digest()

if "--check" in sys.argv[1:]:
    try:
        with open(OUT, "rb") as f:
            found = re.search(rb"sources-sha256=([0-9a-f]{64})", f.read())
    except OSError:
        found = None
    if not found or found.group(1).decode() != DIGEST:
        sys.exit(f"{os.path.relpath(OUT, REPO)} is {'missing' if not os.path.exists(OUT) else 'out of date'}: "
                 "run make docs-pdf and commit it")
    print(f"{os.path.relpath(OUT, REPO)} is current")
    sys.exit(0)

from html import escape as _esc  # noqa: E402

from markdown_it import MarkdownIt  # noqa: E402
from reportlab import rl_config  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.fonts import addMapping  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,  # noqa: E402
                                Preformatted, Table, TableStyle, ListFlowable, ListItem, Image, NextPageTemplate,
                                CondPageBreak)
from reportlab.platypus.flowables import HRFlowable  # noqa: E402
from reportlab.platypus.tableofcontents import TableOfContents  # noqa: E402

rl_config.invariant = 1   # no creation date or random document id: same sources, same bytes


# ------------------------------------------------------------------ fonts
DEJAVU = "/usr/share/fonts/truetype/dejavu/"   # Ubuntu: fonts-dejavu-core (and -extra for the obliques)
VERA = os.path.join(os.path.dirname(rl_config.__file__), "fonts")


def font(*candidates):
    for c in candidates:
        if os.path.exists(c):
            return c
    sys.exit(f"font not found: {candidates[0]} (sudo apt install fonts-dejavu-core)")


for name, path in [("Sans", font(DEJAVU + "DejaVuSans.ttf")), ("Sans-Bold", font(DEJAVU + "DejaVuSans-Bold.ttf")),
                   ("Sans-Oblique", font(DEJAVU + "DejaVuSans-Oblique.ttf", os.path.join(VERA, "VeraIt.ttf"))),
                   ("Sans-BoldOblique", font(DEJAVU + "DejaVuSans-BoldOblique.ttf", os.path.join(VERA, "VeraBI.ttf"))),
                   ("Mono", font(DEJAVU + "DejaVuSansMono.ttf")), ("Mono-Bold", font(DEJAVU + "DejaVuSansMono-Bold.ttf")),
                   ("Mono-Oblique", font(DEJAVU + "DejaVuSansMono-Oblique.ttf", DEJAVU + "DejaVuSansMono.ttf")),
                   ("Mono-BoldOblique", font(DEJAVU + "DejaVuSansMono-BoldOblique.ttf",
                                             DEJAVU + "DejaVuSansMono-Bold.ttf"))]:
    pdfmetrics.registerFont(TTFont(name, path))
for fam in ("Sans", "Mono"):
    addMapping(fam, 0, 0, fam)
    addMapping(fam, 1, 0, fam + "-Bold")
    addMapping(fam, 0, 1, fam + "-Oblique")
    addMapping(fam, 1, 1, fam + "-BoldOblique")

ACCENT = colors.HexColor("#1F4E79")
CODE_BG = colors.HexColor("#F3F4F6")
RULE = colors.HexColor("#C9D3DF")
BAND = colors.HexColor("#EEF3F8")

base = ParagraphStyle("base", fontName="Sans", fontSize=9.5, leading=13.2, spaceAfter=5)
S = {
    "body": base,
    "part": ParagraphStyle("part", parent=base, fontName="Sans-Bold", fontSize=26, leading=32, textColor=ACCENT,
                           spaceBefore=180, spaceAfter=10, alignment=TA_CENTER),
    "h1": ParagraphStyle("h1", parent=base, fontName="Sans-Bold", fontSize=18, leading=23, textColor=ACCENT,
                         spaceBefore=0, spaceAfter=10),
    "h2": ParagraphStyle("h2", parent=base, fontName="Sans-Bold", fontSize=13, leading=17, textColor=ACCENT,
                         spaceBefore=12, spaceAfter=5),
    "h3": ParagraphStyle("h3", parent=base, fontName="Sans-Bold", fontSize=11, leading=15, spaceBefore=9, spaceAfter=4),
    "h4": ParagraphStyle("h4", parent=base, fontName="Sans-Bold", fontSize=9.5, leading=13, spaceBefore=6, spaceAfter=3),
    "cell": ParagraphStyle("cell", parent=base, fontSize=7.8, leading=10, spaceAfter=0),
    "cellhead": ParagraphStyle("cellhead", parent=base, fontName="Sans-Bold", fontSize=7.8, leading=10, spaceAfter=0,
                               textColor=colors.white),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.2, leading=8.6, backColor=CODE_BG, borderPadding=(4, 5, 4, 5),
                           spaceBefore=5, spaceAfter=9, leftIndent=5, rightIndent=5),
    "quote": ParagraphStyle("quote", parent=base, leftIndent=14, textColor=colors.HexColor("#444444")),
    "src": ParagraphStyle("src", parent=base, fontName="Mono", fontSize=7.5, textColor=colors.HexColor("#777777"),
                          spaceAfter=8),
    "title": ParagraphStyle("title", parent=base, fontName="Sans-Bold", fontSize=30, leading=36, textColor=ACCENT,
                            alignment=TA_CENTER, spaceBefore=200, spaceAfter=16),
    "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=12, leading=17, alignment=TA_CENTER,
                               textColor=colors.HexColor("#444444")),
    "toctitle": ParagraphStyle("toctitle", parent=base, fontName="Sans-Bold", fontSize=18, leading=23,
                               textColor=ACCENT, spaceAfter=12),
}
PAGE_W, PAGE_H = A4
MARGIN = 2 * cm
TEXT_W = PAGE_W - 2 * MARGIN


def source(doc):
    if doc == INTRO:
        return open(INTRO_FILE).read()
    text = open(os.path.join(REPO, doc)).read()
    if doc == "README.md":   # the link to this PDF itself
        text = re.sub(r"^.*vnap-secure-documentation\.pdf.*\n\n?", "", text, flags=re.M)
    return text


def doc_id(doc):
    return "d_" + re.sub(r"[^A-Za-z0-9]", "_", doc)


def slugify(text):
    s = text.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")


def inline_text(tok):
    return "".join(c.content for c in (tok.children or []) if c.type in ("text", "code_inline"))


# pre-pass: heading anchors of every document
ANCHORS = {}
for doc in [INTRO] + DOCS:
    seen, toks = {}, md.parse(source(doc))
    for i, t in enumerate(toks):
        if t.type == "heading_open":
            slug = slugify(inline_text(toks[i + 1]))
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            ANCHORS[(doc, slug if n == 0 else f"{slug}-{n}")] = None


def resolve(doc, href):
    """Internal destination (#name) or external URL for a link in `doc`; None for plain text."""
    if re.match(r"^(https?|mailto):", href):
        return href
    path, _, frag = href.partition("#")
    target = doc if not path else os.path.normpath(os.path.join(os.path.dirname(doc), path))
    if target in DOCS or target == doc:
        if frag and (target, frag) in ANCHORS:
            return "#" + doc_id(target) + "__" + frag
        return "#" + doc_id(target)
    if os.path.exists(os.path.join(REPO, target)):
        return GITHUB + target + (("#" + frag) if frag else "")
    return None


# ------------------------------------------------------------------ inline rendering
def render_inline(doc, children):
    out, links = [], []
    for c in children or []:
        t = c.type
        if t == "text":
            out.append(_esc(c.content, quote=False))
        elif t == "code_inline":
            out.append(f'<font face="Mono" color="#8B1A1A">{_esc(c.content, quote=False)}</font>')
        elif t == "strong_open":
            out.append("<b>")
        elif t == "strong_close":
            out.append("</b>")
        elif t == "em_open":
            out.append("<i>")
        elif t == "em_close":
            out.append("</i>")
        elif t == "s_open":
            out.append("<strike>")
        elif t == "s_close":
            out.append("</strike>")
        elif t == "softbreak":
            out.append(" ")
        elif t == "hardbreak":
            out.append("<br/>")
        elif t == "link_open":
            dest = resolve(doc, c.attrs.get("href", ""))
            links.append(dest)
            if dest:
                out.append(f'<a href="{_esc(dest)}" color="#1F5FAF">')
        elif t == "link_close":
            if links.pop():
                out.append("</a>")
        elif t == "image":
            out.append(_esc(c.content or "", quote=False))
        elif t == "html_inline":
            if re.match(r"<br\s*/?>", c.content, re.I):
                out.append("<br/>")
    return "".join(out)


# ------------------------------------------------------------------ blocks
def code_block(text):
    width_chars = int((TEXT_W - 20) / (pdfmetrics.stringWidth("M", "Mono", 7.2)))
    lines = []
    for line in text.rstrip("\n").split("\n"):
        line = line.replace("\t", "    ")
        while len(line) > width_chars:
            cut = line.rfind(" ", 0, width_chars)
            cut = cut if cut > width_chars // 2 else width_chars   # break at a space where there is one
            lines.append(line[:cut])
            line = "    " + line[cut:].lstrip(" ")
        lines.append(line)
    return Preformatted("\n".join(lines), S["code"])


def table_flowable(doc, rows, header):
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    # column widths from content length, with a floor so short columns stay readable
    plain = [[re.sub(r"<[^>]+>", "", c) for c in r] for r in rows]
    weight = []
    for j in range(ncols):
        lens = sorted(len(r[j]) for r in plain)
        longest = lens[-1] if lens else 1
        weight.append(min(max(longest, 6), 60) ** 0.85)
    total = sum(weight)
    widths = [max(TEXT_W * w / total, 1.6 * cm) for w in weight]
    scale = TEXT_W / sum(widths)
    widths = [w * scale for w in widths]
    data = []
    for i, r in enumerate(rows):
        style = S["cellhead"] if (header and i == 0) else S["cell"]
        data.append([Paragraph(c or "", style) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), ACCENT))
    for i in range(1 if header else 0, len(data)):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), BAND))
    t.setStyle(TableStyle(cmds))
    return t


def html_block(doc, html):
    """The README's screenshot table: show its images two per row with their captions."""
    imgs = re.findall(r'<img[^>]+src="([^"]+)"', html)
    if not imgs:
        return []
    caps = re.findall(r"<sub>([^<]*)</sub>", html)
    cells = []
    for k, src in enumerate(imgs):
        path = os.path.normpath(os.path.join(REPO, os.path.dirname(doc), src))
        if not os.path.exists(path):
            continue
        w = TEXT_W / 2 - 10
        img = Image(path)
        img.drawHeight = w * img.imageHeight / img.imageWidth
        img.drawWidth = w
        cells.append([img, Paragraph(caps[k] if k < len(caps) else "", ParagraphStyle("cap", parent=S["cell"],
                                                                                      alignment=TA_CENTER))])
    rows = []
    for k in range(0, len(cells), 2):
        pair = cells[k:k + 2] + ([["", ""]] if len(cells[k:k + 2]) == 1 else [])
        rows.append([pair[0][0], pair[1][0]])
        rows.append([pair[0][1], pair[1][1]])
    t = Table(rows, colWidths=[TEXT_W / 2] * 2)
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [t, Spacer(1, 6)]


class Heading(Paragraph):
    """A paragraph that registers a bookmark, an outline entry and (for parts and chapters) a TOC entry."""

    def __init__(self, text, style, level, key, toc_text=None):
        super().__init__(f'<a name="{key}"/>{text}', style)
        self.level, self.key, self.toc_text = level, key, toc_text or re.sub(r"<[^>]+>", "", text)


def convert(doc, part_level=1):
    """Flowables for one document. Its first H1 is the chapter title."""
    toks = md.parse(source(doc))
    out, stack, seen = [], [], {}
    did = doc_id(doc)
    first_h1 = True
    i = 0

    def para_style():
        return S["quote"] if "blockquote" in stack else S["body"]

    while i < len(toks):
        t = toks[i]
        if t.type == "heading_open":
            lvl = int(t.tag[1])
            text = inline_text(toks[i + 1])
            slug = slugify(text)
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            key = f"{did}__{slug if n == 0 else f'{slug}-{n}'}"
            body = render_inline(doc, toks[i + 1].children)
            if lvl == 1 and first_h1:
                first_h1 = False
                out.append(Heading(body, S["h1"], part_level, did))
                out.append(Paragraph(_esc(doc if doc != INTRO else "written for this document"), S["src"]))
                out[-2].extra_key = key
            else:
                style = S["h2"] if lvl <= 2 else S["h3"] if lvl == 3 else S["h4"]
                level = part_level + (1 if lvl <= 2 else 2)
                if lvl <= 3:
                    out.append(CondPageBreak(3 * cm))
                out.append(Heading(body, style, level, key))
            i += 3
            continue
        if t.type in ("paragraph_open",):
            body = render_inline(doc, toks[i + 1].children)
            if stack and stack[-1] in ("bullet", "ordered"):
                out.append(("li_para", body))
            else:
                out.append(Paragraph(body, para_style()))
            i += 3
            continue
        if t.type in ("fence", "code_block"):
            out.append(code_block(t.content))
            i += 1
            continue
        if t.type == "hr":
            out.append(HRFlowable(width="100%", color=RULE, spaceBefore=4, spaceAfter=8))
            i += 1
            continue
        if t.type == "html_block":
            out.extend(html_block(doc, t.content))
            i += 1
            continue
        if t.type == "blockquote_open":
            stack.append("blockquote")
            i += 1
            continue
        if t.type == "blockquote_close":
            stack.pop()
            i += 1
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            # collect the whole list and build it recursively
            depth, j = 0, i
            while True:
                if toks[j].type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                elif toks[j].type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(build_list(doc, toks[i:j + 1]))
            i = j + 1
            continue
        if t.type == "table_open":
            rows, row, header, j = [], None, False, i + 1
            while toks[j].type != "table_close":
                tt = toks[j]
                if tt.type == "thead_open":
                    header = True
                elif tt.type == "tr_open":
                    row = []
                elif tt.type == "tr_close":
                    rows.append(row)
                elif tt.type == "inline":
                    row.append(render_inline(doc, tt.children))
                j += 1
            out.append(table_flowable(doc, rows, header))
            out.append(Spacer(1, 7))
            i = j + 1
            continue
        i += 1
    return out


def build_list(doc, toks):
    ordered = toks[0].type == "ordered_list_open"
    start = int(toks[0].attrs.get("start", 1)) if ordered else 1
    items, cur, i = [], None, 1
    while i < len(toks) - 1:
        t = toks[i]
        if t.type == "list_item_open":
            cur = []
            i += 1
            continue
        if t.type == "list_item_close":
            items.append(cur)
            i += 1
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            depth, j = 0, i
            while True:
                if toks[j].type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                elif toks[j].type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            cur.append(build_list(doc, toks[i:j + 1]))
            i = j + 1
            continue
        if t.type == "paragraph_open":
            cur.append(Paragraph(render_inline(doc, toks[i + 1].children), ParagraphStyle("li", parent=S["body"],
                                                                                           spaceAfter=2)))
            i += 3
            continue
        if t.type in ("fence", "code_block"):
            cur.append(code_block(t.content))
            i += 1
            continue
        if t.type == "table_open":
            j = i
            while toks[j].type != "table_close":
                j += 1
            sub = convert_tokens_table(doc, toks[i:j + 1])
            cur.append(sub)
            i = j + 1
            continue
        i += 1
    li = [ListItem(c if len(c) != 1 else c[0], leftIndent=13) for c in items if c]
    kw = dict(bulletType="1", start=start, bulletFormat="%s.") if ordered else dict(bulletType="bullet", start="•")
    return ListFlowable(li, leftIndent=13, bulletFontName="Sans", bulletFontSize=9, spaceAfter=5, **kw)


def convert_tokens_table(doc, toks):
    rows, row, header = [], None, False
    for tt in toks:
        if tt.type == "thead_open":
            header = True
        elif tt.type == "tr_open":
            row = []
        elif tt.type == "tr_close":
            rows.append(row)
        elif tt.type == "inline":
            row.append(render_inline(doc, tt.children))
    return table_flowable(doc, rows, header)


# ------------------------------------------------------------------ document template
class Doc(BaseDocTemplate):
    def __init__(self, path, **kw):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                         bottomMargin=MARGIN, title=TITLE, author="VNAP-Secure",
                         keywords=f"sources-sha256={DIGEST}", **kw)
        frame = Frame(MARGIN, MARGIN, TEXT_W, PAGE_H - 2 * MARGIN, id="f")
        self.addPageTemplates([PageTemplate("cover", [frame], onPage=lambda c, d: None),
                               PageTemplate("body", [frame], onPage=self.decorate)])
        self.part_name = ""
        self.last_level = -1

    def beforeDocument(self):
        self.part_name, self.last_level = "", -1   # multiBuild runs several passes

    def decorate(self, canv, doc):
        canv.saveState()
        canv.setFont("Sans", 7.5)
        canv.setFillColor(colors.HexColor("#777777"))
        canv.drawString(MARGIN, PAGE_H - MARGIN + 18, TITLE + (f"  ·  {self.part_name}" if self.part_name else ""))
        canv.drawRightString(PAGE_W - MARGIN, MARGIN - 22, str(doc.page))
        canv.setStrokeColor(RULE)
        canv.setLineWidth(0.4)
        canv.line(MARGIN, PAGE_H - MARGIN + 14, PAGE_W - MARGIN, PAGE_H - MARGIN + 14)
        canv.restoreState()

    def afterFlowable(self, f):
        if isinstance(f, Heading):
            self.canv.bookmarkPage(f.key)
            if getattr(f, "extra_key", None):
                self.canv.bookmarkPage(f.extra_key)
            level = min(f.level, self.last_level + 1)   # an outline may not skip a level
            self.last_level = level
            self.canv.addOutlineEntry(f.toc_text, f.key, level=level, closed=level >= 1)
            if f.level <= 1:
                self.notify("TOCEntry", (f.level, f.toc_text, self.page, f.key))
            if f.level == 0:
                self.part_name = f.toc_text


# the date of the newest committed change to the sources (no clock, so a rebuild is reproducible)
changed = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%cs", "--", *DOCS, os.path.relpath(INTRO_FILE, REPO)],
                         capture_output=True, text=True).stdout.strip() or "uncommitted"

story = [Paragraph(TITLE, S["title"]),
         Paragraph("Design intention, setup, operation, reference, patch set and results", S["subtitle"]),
         Spacer(1, 30),
         Paragraph(f"Compiled from the Markdown documentation in the repository<br/>sources last changed "
                   f"{changed} · digest {DIGEST[:12]}", S["subtitle"]),
         NextPageTemplate("body"), PageBreak()]
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("t0", parent=base, fontName="Sans-Bold", fontSize=10.5, leading=15, spaceBefore=7),
                   ParagraphStyle("t1", parent=base, fontSize=9, leading=12, leftIndent=16)]
toc.dotsMinLevel = 0
story += [Paragraph("Contents", S["toctitle"]), toc, PageBreak()]

# introduction: a chapter at level 0
story += convert(INTRO, part_level=0)
story.append(PageBreak())

for part, docs in PARTS:
    story.append(Heading(part, S["part"], 0, "part_" + slugify(part)))
    story.append(PageBreak())
    for k, doc in enumerate(docs):
        story += convert(doc, part_level=1)
        story.append(PageBreak() if k < len(docs) - 1 else Spacer(1, 0))
    story.append(PageBreak())
if isinstance(story[-1], PageBreak):
    story.pop()

doc = Doc(OUT)
doc.multiBuild(story)
print(f"{os.path.relpath(OUT, REPO)}: {doc.page} pages")
