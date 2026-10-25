import io
import logging
import uuid
from pathlib import Path

from app.config import settings
from app.repositories.resume_repository import ResumeRepository
from app.repositories.template_repository import TemplateRepository
from app.services import placeholders, storage
from app.services.exceptions import (
    ResumeNotFound,
    UnfilledPlaceholder,
    UnsupportedFormat,
)
from app.services.resume_ops import (
    date_label,
    education_cells,
    education_lines,
    experience_lines,
    extra_entry_line,
    link_label,
    to_plain_text,
    visible_extras,
    with_location,
)

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


# Typographic characters that are NOT in Latin-1 but have an obvious ASCII
# equivalent. Without this every one of them renders as "?" in the PDF —
# which matters because real resumes are pasted out of Word and Google Docs,
# both of which autocorrect quotes and dashes by default.
_TRANSLITERATE = {
    # Dashes and curly quotes are NOT downgraded: the placeholder syntax
    # "[__]" and the banner template's "degree — school" both write U+2014,
    # and the embedded DejaVu faces carry it. Downgrading it was Latin-1
    # behaviour; "resume - Resume Worded" is not "resume — Resume Worded".
    "\u2212": "-", "\u2012": "-",  # minus/figure-dash: not in the subset
    "\u201a": ",", "\u201b": "'",  # low commas: never intentional
    "\u201e": '"', "\u201f": '"',
    # "\u2022" and "\u00b7" are NO LONGER downgraded: the embedded faces
    # carry them, and _BULLET_RE accepts \u2022 on re-import -- which the
    # old "\u00b7" workaround did not, so our own bullets did not read
    # back as bullets.
    "\u2023": "\u2022",
    "\u2010": "-", "\u2011": "-", "\u00a0": " ", "\u202f": " ",
    "\u2009": " ", "\u200b": "", "\ufeff": "",
    "\u2192": "->", "\u2190": "<-", "\u2264": "<=", "\u2265": ">=",
    "\u00d7": "x", "\u2122": "(TM)", "\u2033": '"', "\u2032": "'",
}

_TRANSLITERATE_TABLE = str.maketrans(_TRANSLITERATE)


#: The embedded faces, registered once per FPDF instance.
_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"

#: Small monochrome outline marks (feather-style, 24x24) drawn as VECTORS via
#: fpdf2's SVG support -- never as text. That is the load-bearing property:
#: a glyph typed into a cell would be extracted back out with the contact
#: line or the heading, and the icon would end up inside the parsed email /
#: the section the segmenter looks for. Paths carry no text run, so a
#: decorated resume re-imports byte-identical to an undecorated one.
#: `currentColor` is substituted with the template accent at render time.
_HEADING_ICONS = {
    "summary": "user",
    "experience": "briefcase",
    "projects": "folder",
    "education": "grad-cap",
    "skills": "code",
    "certifications": "award",
}
_ICON_CACHE: dict[tuple[str, str], bytes] = {}


def _icon_bytes(name: str, color: str) -> bytes:
    key = (name, color)
    cached = _ICON_CACHE.get(key)
    if cached is None:
        raw = (_ICON_DIR / f"{name}.svg").read_bytes()
        if b"currentColor" not in raw:
            raise ValueError(f"icon {name!r} has no currentColor to substitute")
        cached = raw.replace(b"currentColor", color.encode("ascii"))
        _ICON_CACHE[key] = cached
    return cached


def _draw_icon(pdf, name: str, x: float, y: float, size: float, color: str) -> None:
    pdf.image(io.BytesIO(_icon_bytes(name, color)), x=x, y=y, w=size, h=size)


def _contact_icon(line: str, contact: dict) -> str:
    if line == contact.get("email"):
        return "mail"
    if line == contact.get("phone"):
        return "phone"
    return "map-pin"


def _link_icon(url: str) -> str:
    u = url.lower()
    if "linkedin.com" in u:
        return "linkedin"
    if "github.com" in u or "gitlab.com" in u:
        return "git-branch"
    return "globe"


_ICON_W = 3.6
_ICON_GAP = 1.2


def _measure_icon_row(pdf, items: list, sep: str) -> tuple[float, float]:
    """Total width of one icon-decorated row: icons+texts+separators.

    Measured up front because a centred/right-aligned row must know its own
    width before it can place itself -- the same reason the icon-less rows
    measure first. Only the caller's `<= epw` check keeps an over-long row
    from running off the page (it falls back to the old layout instead).
    """
    sep_w = pdf.get_string_width(sep)
    text_w = sum(pdf.get_string_width(t) for _, t, _ in items)
    icons_w = sum(_ICON_W + _ICON_GAP for icon, _, _ in items if icon)
    return text_w + icons_w + sep_w * max(len(items) - 1, 0), sep_w


def _draw_icon_row(
    pdf, items: list, sep: str, total: float, sep_w: float, *,
    align: str, height: float, color: str,
) -> None:
    """Lay out [(icon|None, text, url|None)] left-to-right on one baseline.

    fpdf2 attaches a link to a cell, not a substring, so each item is its own
    cell; the x cursor is advanced by hand, mirroring the icon-less rows.
    """
    start = pdf.l_margin
    if align == "C":
        start += (pdf.epw - total) / 2
    elif align == "R":
        start += pdf.epw - total
    x = max(pdf.l_margin, start)
    y0 = pdf.get_y()
    for i, (icon, text, url) in enumerate(items):
        if i:
            pdf.set_xy(x, y0)
            pdf.cell(sep_w, height, sep)
            x += sep_w
        if icon:
            _draw_icon(pdf, icon, x, y0 + 1.3, _ICON_W, color)
            x += _ICON_W + _ICON_GAP
        pdf.set_xy(x, y0)
        pdf.cell(pdf.get_string_width(text), height, text, link=url or "")
        x += pdf.get_string_width(text)
    pdf.set_y(y0 + height)


_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_FACES = {
    ("sans", ""): "DejaVuSans.ttf",
    ("sans", "B"): "DejaVuSans-Bold.ttf",
    ("sans", "I"): "DejaVuSans-Oblique.ttf",
    ("sans", "BI"): "DejaVuSans-BoldOblique.ttf",
    ("serif", ""): "DejaVuSerif.ttf",
    ("serif", "B"): "DejaVuSerif-Bold.ttf",
    ("serif", "I"): "DejaVuSerif-Italic.ttf",
    ("serif", "BI"): "DejaVuSerif-BoldItalic.ttf",
}
#: Codepoints the subset carries. Anything outside it would render as a
#: hollow .notdef box, which looks like a bug to the person holding the
#: printout, so it is transliterated or dropped instead.
_SUBSET_EXTRA = frozenset(
    "\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u00b7\u25cf\u2026"
    "\u20ac\u00a3\u00a5\u2709\u260e\u2706\u2713\u2714\u2605\u2606"
    "\u2302\U0001f4cd\u00a9\u00ae\u2122"
)


def _register_fonts(pdf) -> None:
    for (family, style), filename in _FONT_FACES.items():
        pdf.add_font(family, style, str(_FONT_DIR / filename))


def _latin1(text: str) -> str:
    """Keep text inside the glyph repertoire we actually ship.

    The name is a leftover: the exporter is no longer Latin-1. It embeds
    subset DejaVu faces, so real bullets, en/em dashes, curly quotes and
    the contact icons all render now -- those used to raise
    FPDFUnicodeEncodingException with fpdf2's core fonts.

    What survives is still a repertoire, not all of Unicode. The subset is
    Latin-1 + Latin Extended-A + punctuation + the symbols the templates
    draw, because vendoring whole faces costs ~4MB for glyphs a resume will
    never use. Anything outside it is transliterated, and dropped only if
    that fails -- a missing glyph must never become a hollow box on
    somebody's printed CV.
    """
    text = text.translate(_TRANSLITERATE_TABLE)
    return "".join(
        ch
        for ch in text
        if ch in _SUBSET_EXTRA or ord(ch) < 0x180 or ch in "\n\t"
    )


def _safe_filename(title: str, fmt: str) -> str:
    """HTTP headers are latin-1 only.

    A title like "Priya Sharma — SRE" contains an em dash and raises
    UnicodeEncodeError when placed in Content-Disposition. Reduce to a safe
    ASCII slug.
    """
    import re
    import unicodedata

    normalised = unicodedata.normalize("NFKD", title or "resume")
    ascii_only = normalised.encode("ascii", errors="ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("_")
    return f"{slug or 'resume'}.{fmt}"


# Fallback when a resume points at a template row that no longer exists.
# Mirrors the "modern" tokens in seed.py.
DEFAULT_TOKENS = {
    "font": "sans",
    "heading": "rule",
    "name_align": "left",
    "accent": "#4737ff",
    "density": "normal",
    "caps": True,
    "divider": False,
    # "stacked" = name over contact; "split" = name left, contact right.
    "header": "stacked",
    # "stacked" = role on its own line above the company (the common
    # real-world layout); "inline" = "Role - Company" on one line.
    "entry": "stacked",
    "entry_lead": "role",
    #: Separator between role and company on an inline entry row.
    "entry_sep": " - ",
    #: Outline icons beside section headings and contact items, drawn as
    #: vectors (see _ICON_DIR) so re-import never sees them. Reference
    #: templates only; default off.
    "icons": False,
    #: "school" headlines the institution and drops the degree beneath it
    #: (fix-list defect 9, per the executive reference); "degree" is the
    #: historical ordering. Stacked education only -- the inline fold does
    #: not reverse.
    "edu_lead": "degree",
    #: Reference templates set the floated-right dates row in italic;
    #: "plain" keeps the historical look.
    "dates_style": "plain",
    #: "inline" folds the school into the degree row: "Degree — School".
    "edu": "stacked",
    "layout": "single",
    # Skills laid out in N columns, as printed templates do.
}

# fpdf2 core fonts. "serif" maps to Times, "sans" to Helvetica -- both are
# built in, so no font files ship with the app.
#: Template font token -> embedded family. Both are subset DejaVu
#: faces; the core "Helvetica"/"Times" are no longer used because
#: they cannot print a bullet.
_PDF_FONTS = {"sans": "sans", "serif": "serif"}

# density -> (line height, gap before a heading)
# (line height, gap above a heading) in mm. Tightened so more of a resume
# fits on the first page. Side margins are deliberately NOT reduced: fpdf2's
# 10mm default is already at the low end of what print and PDF text
# extraction handle well, so the space is bought from leading instead, which
# costs nothing legible.
_SIDE_MARGIN = 15.0
_BULLET_INDENT = 4.6
_ENTRY_GAP = 2.4
#: Baseline offset of the split header's right column. Two columns
#: fuse in text extraction only when they share a baseline.
_SPLIT_STAGGER = 2.6
_SPLIT_LINE_H = 4.6
#: Right rail as a fraction of the usable width, and the gutter beside
#: it. The gutter is what makes the page detectably two-column.
_RAIL_FRACTION = 0.30
_RAIL_GUTTER = 15.0

_DENSITY = {"dense": (4.0, 1.6), "normal": (4.5, 2.2), "airy": (5.2, 3.4)}


logger = logging.getLogger(__name__)


def _gutter_is_detectable(raw: bytes) -> bool:
    """Would the importer read this PDF as two columns?

    Imported lazily and defensively: a layout decision must never be the
    reason an export fails, so any problem here means "treat it as not
    detectable" and fall back to the layout that always reads correctly.
    """
    try:
        import io as _io

        import pdfplumber

        from app.services.parsing import _column_split_x

        with pdfplumber.open(_io.BytesIO(raw)) as doc:
            return all(_column_split_x(page) is not None for page in doc.pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("column check failed, falling back to one column: %s", exc)
        return False


def _render_two_columns(pdf, main, rail) -> None:
    """A wide main column with a narrow right rail.

    fpdf2 has no flow layout, so the two columns are rendered one after the
    other by moving the page margins and rewinding the cursor -- the main
    column first, so it also leads in the content stream.

    THE GUTTER IS LOAD-BEARING, not decoration. Our own `_column_split_x`
    finds a two-column page by looking for a vertical band that no word
    crosses; without a real gap the page extracts as interleaved rows and
    the resume becomes nonsense. _RAIL_GUTTER is what that detector keys on.

    Page breaks: the rail is always drawn on the page the body started on.
    A rail long enough to overflow is clipped rather than wrapped onto a
    second page, because a sidebar that continues overleaf reads as orphaned
    content. Keeping the rail short is the template's job.
    """
    full_left, full_right = pdf.l_margin, pdf.r_margin
    page_w = pdf.w
    usable = page_w - full_left - full_right
    rail_w = usable * _RAIL_FRACTION
    main_w = usable - rail_w - _RAIL_GUTTER

    start_page, start_y = pdf.page, pdf.get_y()

    # --- main column: shrink the right margin so every existing helper,
    #     which measures against pdf.epw, simply works.
    pdf.set_right_margin(full_right + rail_w + _RAIL_GUTTER)
    pdf.set_x(full_left)
    for section in main:
        section()
    main_end_page, main_end_y = pdf.page, pdf.get_y()

    # --- rail: back to where the body began, on the right.
    pdf.set_right_margin(full_right)
    pdf.set_left_margin(full_left + main_w + _RAIL_GUTTER)
    pdf.page = start_page
    pdf.set_y(start_y)
    # Auto page break would push a long rail onto a new page mid-render and
    # leave the cursor somewhere unrelated; it is restored below.
    was_auto = pdf.auto_page_break
    pdf.set_auto_page_break(False)
    for section in rail:
        section()
    rail_end_y = pdf.get_y()

    pdf.set_auto_page_break(was_auto, pdf.b_margin)
    pdf.set_left_margin(full_left)
    pdf.page = main_end_page
    pdf.set_y(main_end_y if main_end_page > start_page else max(main_end_y, rail_end_y))
    pdf.set_x(full_left)

    del start_y, main_end_y, rail_end_y  # kept above for readability only


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    """#4737ff -> (71, 55, 255). Returns None for "" so callers can skip."""
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _add_hyperlink(paragraph, url: str, text: str) -> None:
    """Append a real hyperlink run to a python-docx paragraph.

    python-docx has no hyperlink API, so the relationship and the `w:hyperlink`
    element are built by hand. Styling is explicit rather than via the
    "Hyperlink" character style, which is not guaranteed to exist in a
    document built from the default template.
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    colour = OxmlElement("w:color")
    colour.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(colour)
    props.append(underline)
    run.append(props)

    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


class ExportService:
    """Renders PDF / DOCX / TXT. Results are cached per resume version.

    The PDF honours the resume's template: design_tokens drive the typeface,
    heading treatment, alignment, accent colour and density, so the downloaded
    file matches the template chosen in the UI. Before this the exporter was
    hardcoded to Helvetica and every template produced an identical PDF.
    """

    def __init__(
        self,
        resumes: ResumeRepository,
        templates: TemplateRepository | None = None,
    ) -> None:
        self.resumes = resumes
        # Optional so existing construction sites keep working; without it the
        # exporter simply falls back to the default tokens.
        self.templates = templates

    def _tokens_for(self, template_key: str) -> dict:
        if self.templates is None:
            return DEFAULT_TOKENS
        template = self.templates.get_by_key(template_key)
        if template is None or not template.design_tokens:
            return DEFAULT_TOKENS
        # Merge over the default so a partially-populated row cannot raise
        # KeyError deep inside the renderer.
        return {**DEFAULT_TOKENS, **template.design_tokens}

    def export(self, resume_id: uuid.UUID, user_id: uuid.UUID, fmt: str) -> tuple[bytes, str, str]:
        fmt = fmt.lower()
        if fmt not in FORMATS:
            raise UnsupportedFormat(f"{fmt}: expected pdf, docx or txt")
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))

        # An unfilled [__] must never leave the building.
        #
        # The quantify rewrite deliberately puts a blank in the resume rather
        # than inventing a figure, which means the document is briefly in a
        # state that is WORSE to send than the missing metric it fixes. This
        # refuses rather than warns: a warning on an export is read after the
        # file is already downloaded and attached.
        #
        # Before the cache lookup, though not because correctness needs it:
        # the key already contains version_cursor, so editing a placeholder
        # in always misses. It sits here so the guard does not silently
        # become dependent on how the key is composed.
        unfilled = placeholders.find_in_resume(resume.structured_data or {})
        if unfilled:
            raise UnfilledPlaceholder(
                f"This resume still has {len(unfilled)} unfilled "
                f"{placeholders.PLACEHOLDER} placeholder"
                f"{'s' if len(unfilled) > 1 else ''}. Replace "
                f"{'them' if len(unfilled) > 1 else 'it'} with your real "
                "figure before exporting.",
                refs=unfilled,
            )

        # The template is part of the cache identity: switching template must
        # produce a different PDF, and a key without it would serve the old
        # styling from the bucket forever.
        key = f"{user_id}/{resume_id}/v{resume.version_cursor}-{resume.template_key}.{fmt}"
        filename = _safe_filename(resume.title, fmt)

        if storage.object_exists(settings.s3_bucket_exports, key):
            return storage.get_object(settings.s3_bucket_exports, key), FORMATS[fmt], filename

        data = resume.structured_data or {}
        if fmt == "txt":
            # Plain text is deliberately style-free: it exists for ATS paste
            # boxes, so the template must NOT affect it.
            payload = to_plain_text(data).encode("utf-8")
        elif fmt == "pdf":
            payload = self._render_pdf(data, self._tokens_for(resume.template_key))
        else:
            payload = self._render_docx(data)

        storage.put_object(settings.s3_bucket_exports, key, payload, FORMATS[fmt])
        return payload, FORMATS[fmt], filename

    def _render_pdf(self, data: dict, tokens: dict | None = None) -> bytes:
        from fpdf import FPDF

        tokens = {**DEFAULT_TOKENS, **(tokens or {})}
        font = _PDF_FONTS.get(tokens["font"], "sans")
        line_h, head_gap = _DENSITY.get(tokens["density"], _DENSITY["normal"])
        accent = _hex_to_rgb(tokens["accent"])
        centred = tokens["name_align"] == "center"
        style = tokens["heading"]

        pdf = FPDF()
        _register_fonts(pdf)
        # Side margins are wider than fpdf2's 10mm default. A 190mm measure
        # is simply too long to read comfortably -- printed resumes run about
        # 165-175mm. The leading stays tight, so this costs far less height
        # than it buys in legibility.
        pdf.set_margins(_SIDE_MARGIN, 12, _SIDE_MARGIN)
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_page()

        # A sidebar's HEADER is confined to the main column too, not just
        # its body. _column_split_x requires a vertical band that no word
        # crosses anywhere on the page, so one full-width contact line at
        # the top is enough to hide the gutter and make the whole page
        # extract interleaved. Keeping the header inside the left column
        # also puts the contact details first in reading order, which is
        # where a parser looks for them.
        sidebar = tokens.get("layout") == "sidebar"
        use_icons = bool(tokens.get("icons"))
        icon_color = tokens.get("accent") or "#3A3A3A"
        if sidebar:
            usable = pdf.w - pdf.l_margin - pdf.r_margin
            pdf.set_right_margin(
                pdf.r_margin + usable * _RAIL_FRACTION + _RAIL_GUTTER
            )

        def body(text: str) -> None:
            """Always reset x first.

            cell() leaves the cursor at the right margin; a following
            multi_cell(0, ...) then has zero available width and fpdf2 raises
            "Not enough horizontal space to render a single character".

            align="L" is load-bearing. fpdf2's multi_cell justifies by
            default, and justification without hyphenation opens rivers of
            white space down the page -- it is the single clearest tell of
            machine-set text. Every printed resume is ragged-right.
            """
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.epw, line_h, _latin1(text), align="L")

        def bullet_item(text: str) -> None:
            """A bullet whose wrapped lines hang under the text, not the dot.

            Previously bullets were "  - " prepended to a full-width
            multi_cell, so the second line of every bullet returned to the
            left margin and the list stopped reading as a list. This is the
            defect that made the output look generated.

            The glyph is U+00B7, set two points larger than the body. A true
            round bullet (U+2022) is NOT available: fpdf2's core fonts are
            strictly Latin-1 and raise on it. It is drawn as text rather than
            as a vector circle on purpose -- our own parser requires a
            literal marker to recognise a bullet, so a drawn dot would make
            an exported resume un-importable.
            """
            y0 = pdf.get_y()
            # 9.5pt, not 13pt bold. The old size was chosen for U+00B7, a
            # mid-height dot; the real U+2022 at that size rides above the
            # line like a superscript.
            pdf.set_font(font, "", 9.5)
            pdf.set_xy(pdf.l_margin + 0.8, y0)
            pdf.cell(_BULLET_INDENT - 0.8, line_h, "\u2022")
            pdf.set_font(font, "", 10)
            pdf.set_xy(pdf.l_margin + _BULLET_INDENT, y0)
            pdf.multi_cell(
                pdf.epw - _BULLET_INDENT,
                line_h,
                _latin1(text),
                align="L",
                new_x="LMARGIN",
                new_y="NEXT",
            )

        contact = data.get("contact", {})
        name = contact.get("name", "Resume")
        # One alignment for the whole contact block. The link row used to be
        # laid out with `align` while the detail row above it used "R" for a
        # split header, so the links orphaned to the far left and the divider
        # rule cut across them.
        headline = contact.get("headline", "")
        bits = [contact.get("email"), contact.get("phone"), contact.get("location")]
        detail_lines = [x for x in bits if x]
        align = "C" if centred else "L"
        split = tokens["header"] == "split" and not centred
        split_rendered_links = False
        detail_align = "R" if split else align

        if split:
            # A TRUE two-column header: name and headline left, contact and
            # links stacked flush right, sharing the same band.
            #
            # This was removed once, for a real reason. The first attempt put
            # both columns on the same baselines, and a PDF text extractor
            # reads across a line, so it came back as
            #
            #     Jane Doe jane@example.com
            #     Senior Engineer Pune, India
            #
            # -- the name fused to the email. That is the worst possible
            # block to corrupt, because a recruiter then cannot make contact.
            #
            # The old comment concluded "there is no clever fix: anything
            # genuinely side by side shares a text line". That is not quite
            # true. Columns only fuse when they share a BASELINE. The right
            # column is therefore offset by half a line and uses a different
            # line height from the left, so no right-hand baseline ever
            # coincides with a left-hand one and every field extracts onto
            # its own line.
            right_w = pdf.epw * 0.42
            left_w = pdf.epw - right_w
            top = pdf.get_y()

            pdf.set_font(font, "B", 22 if tokens["density"] == "airy" else 19)
            if accent:
                pdf.set_text_color(*accent)
            pdf.set_xy(pdf.l_margin, top)
            pdf.cell(left_w, 10, _latin1(name), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            if headline:
                pdf.set_font(font, "", 11)
                pdf.set_x(pdf.l_margin)
                pdf.cell(left_w, 6, _latin1(headline), new_x="LMARGIN", new_y="NEXT")
            left_end = pdf.get_y()

            # Right column. _SPLIT_STAGGER is the baseline offset described
            # above; it is load-bearing, not cosmetic.
            pdf.set_font(font, "", 9)
            right_rows = list(detail_lines)
            y = top + _SPLIT_STAGGER
            for row in right_rows:
                pdf.set_xy(pdf.l_margin + left_w, y)
                pdf.cell(right_w, _SPLIT_LINE_H, _latin1(row), align="R")
                y += _SPLIT_LINE_H
            for url in [x for x in contact.get("links", []) if x]:
                pdf.set_xy(pdf.l_margin + left_w, y)
                pdf.cell(
                    right_w, _SPLIT_LINE_H, _latin1(link_label(url)),
                    align="R", link=url,
                )
                y += _SPLIT_LINE_H

            pdf.set_y(max(left_end, y))
            split_rendered_links = True
        else:
            pdf.set_font(font, "B", 22 if tokens["density"] == "airy" else 19)
            if accent:
                pdf.set_text_color(*accent)
            pdf.cell(0, 10, _latin1(name), new_x="LMARGIN", new_y="NEXT", align=align)
            pdf.set_text_color(0, 0, 0)

            # multi_cell, not cell: cell() overflows rather than wrapping, and
            # in a sidebar template the header is confined to the main column.
            # One word of "Newark, New Jersey" hanging past the column was
            # enough to cross the gutter and hide it from the column detector,
            # which scrambled the entire page.
            if headline:
                pdf.set_font(font, "", 11)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    pdf.epw, 6, _latin1(headline),
                    align=align, new_x="LMARGIN", new_y="NEXT",
                )

            # Contact AND links on one line, as the reference resumes write
            # it: "Pune, India  \u2022  +91 ...  \u2022  email  \u2022  LinkedIn".
            # They used to be two rows, which wasted a line at the top of
            # the page and read as two unrelated blocks. The links are laid
            # out separately below so each stays a real annotation; this
            # row carries only what is not clickable.
            detail = "" if sidebar else "  \u2022  ".join(detail_lines)
            inline_links = (
                []
                if (sidebar or split_rendered_links)
                else [x for x in contact.get("links", []) if x]
            )
            if use_icons and (detail_lines or inline_links):
                pdf.set_font(font, "", 9)
                sep = "  \u2022  "
                items = [
                    (_contact_icon(x, contact), _latin1(x), None)
                    for x in ([] if sidebar else detail_lines)
                ]
                items += [
                    (_link_icon(u), _latin1(link_label(u)), u) for u in inline_links
                ]
                width, sep_w = _measure_icon_row(pdf, items, sep)
                if width <= pdf.epw:
                    _draw_icon_row(
                        pdf, items, sep, width, sep_w,
                        align=align, height=6, color=icon_color,
                    )
                    split_rendered_links = True
                elif detail:
                    # Overlong once the links join in: retry with only the
                    # detail items -- the links then fall through to their own
                    # row, still iconed. Falling back to NO icons here was
                    # tried first; the detail row lost its icons while the
                    # links row below kept its own, which looked like a bug.
                    detail_items = [it for it in items if it[2] is None]
                    width, sep_w = _measure_icon_row(pdf, detail_items, sep)
                    if width <= pdf.epw:
                        _draw_icon_row(
                            pdf, detail_items, sep, width, sep_w,
                            align=align, height=6, color=icon_color,
                        )
                    else:
                        pdf.set_x(pdf.l_margin)
                        pdf.multi_cell(
                            pdf.epw, 6, _latin1(detail),
                            align=align, new_x="LMARGIN", new_y="NEXT",
                        )
            elif detail or inline_links:
                pdf.set_font(font, "", 9)
                sep = "  \u2022  "
                sep_w = pdf.get_string_width(sep)
                pairs = [(url, _latin1(link_label(url))) for url in inline_links]
                link_w = [pdf.get_string_width(lbl) for _, lbl in pairs]
                detail_w = pdf.get_string_width(_latin1(detail)) if detail else 0
                total = detail_w + sum(link_w) + sep_w * (
                    len(pairs) - (0 if detail else 1)
                )
                if total <= pdf.epw:
                    # One row: details then links, sharing a line.
                    #
                    # Laid out by hand because fpdf2 attaches a link to a
                    # cell, not to a substring -- so each address needs its
                    # own cell while still sitting in the same visual row.
                    start = pdf.l_margin
                    if align == "C":
                        start += (pdf.epw - total) / 2
                    elif align == "R":
                        start += pdf.epw - total
                    pdf.set_x(max(pdf.l_margin, start))
                    if detail:
                        pdf.cell(detail_w, 6, _latin1(detail))
                    for i, ((url, label), width) in enumerate(
                        zip(pairs, link_w, strict=True)
                    ):
                        if detail or i:
                            pdf.cell(sep_w, 6, _latin1(sep))
                        pdf.cell(width, 6, label, link=url)
                    pdf.ln(6)
                    split_rendered_links = True
                elif detail:
                    # Too wide for one line: fall back to the old two rows
                    # rather than overflow the margin.
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        pdf.epw, 6, _latin1(detail),
                        align=align, new_x="LMARGIN", new_y="NEXT",
                    )

        links = [] if (split_rendered_links or sidebar) else [
            x for x in contact.get("links", []) if x
        ]
        if links:
            # Brand labels ("LinkedIn", "GitHub") with the URL carried by a
            # real clickable annotation -- what every reference does. Re-import
            # stays safe: the parser harvests the annotation layer, not the
            # text. Unrecognised hosts fall back to their short address.
            #
            # One cell per label, because fpdf2 attaches a link to a cell,
            # not to a substring. That means laying the row out by hand:
            # measure it, then set x so the result still honours the
            # template's alignment.
            pdf.set_font(font, "", 9)
            if use_icons:
                sep = "  |  "
                items = [(_link_icon(u), _latin1(link_label(u)), u) for u in links]
                width, sep_w = _measure_icon_row(pdf, items, sep)
                if width <= pdf.epw:
                    _draw_icon_row(
                        pdf, items, sep, width, sep_w,
                        align=detail_align, height=5, color=icon_color,
                    )
                    links = []

            pairs = [(url, link_label(url)) for url in links]
            sep = "  |  "
            sep_w = pdf.get_string_width(sep)
            widths = [pdf.get_string_width(label) for _, label in pairs]
            total = sum(widths) + sep_w * (len(pairs) - 1)
            if detail_align == "C":
                start = pdf.l_margin + (pdf.epw - total) / 2
            elif detail_align == "R":
                start = pdf.l_margin + pdf.epw - total
            else:
                start = pdf.l_margin
            pdf.set_x(max(pdf.l_margin, start))
            for i, ((url, label), width) in enumerate(zip(pairs, widths, strict=True)):
                if i:
                    pdf.cell(sep_w, 5, _latin1(sep))
                pdf.cell(width, 5, _latin1(label), link=url)
            pdf.ln(5)

        if tokens["divider"]:
            y = pdf.get_y() + 1
            pdf.line(pdf.l_margin, y, pdf.l_margin + pdf.epw, y)
            pdf.ln(2)

        def heading(title: str) -> None:
            pdf.ln(head_gap)
            if accent:
                pdf.set_text_color(*accent)
            pdf.set_font(font, "B", 11)
            label = title.upper() if tokens["caps"] else title
            # Letterspacing an all-caps heading is what makes it read as a
            # label rather than a shouted word. Only for caps: tracking
            # lowercase text just looks loose.
            if tokens["caps"]:
                pdf.set_char_spacing(0.7)
            if use_icons and style in {"rule", "underline", "plain"}:
                icon = _HEADING_ICONS.get(title.lower(), "chevron")
                # Vector, not a text run: extraction and re-import cannot
                # see it (see _ICON_DIR).
                _draw_icon(pdf, icon, pdf.l_margin, pdf.get_y() + 1.5, 4.2, icon_color)
                pdf.set_x(pdf.l_margin + 5.6)
            if style == "sidebar":
                # The bar used to be a literal "| " in the TEXT, so every
                # heading extracted as "| Experience". Our own segmenter --
                # and any ATS -- failed to recognise it, and the entire work
                # history was dropped from the parsed document. A decorative
                # rule must be drawn, never typed.
                pdf.set_draw_color(*(accent or (0, 0, 0)))
                pdf.set_line_width(1.2)
                y0 = pdf.get_y() + 1.2
                pdf.line(pdf.l_margin, y0, pdf.l_margin, y0 + 4.6)
                pdf.set_line_width(0.2)
                pdf.set_draw_color(0, 0, 0)
                pdf.set_x(pdf.l_margin + 2.2)
            elif style == "boxed":
                label = f"  {label}  "
            pdf.cell(0, 7, _latin1(label), new_x="LMARGIN", new_y="NEXT")
            pdf.set_char_spacing(0)
            pdf.set_text_color(0, 0, 0)

            # A rule under the heading, drawn in the accent colour when there
            # is one so the section break reads as deliberate.
            if style in {"rule", "underline"}:
                y = pdf.get_y()
                if accent and style == "rule":
                    pdf.set_draw_color(*accent)
                pdf.line(pdf.l_margin, y, pdf.l_margin + pdf.epw, y)
                pdf.set_draw_color(0, 0, 0)
                pdf.ln(1.5)

            pdf.set_font(font, "", 10)

        def entry_header(primary: str, secondary: str, dates: str) -> None:
            """Title row with the dates flushed right.

            Printed templates put dates hard against the right margin rather
            than trailing the title, which is what makes a resume scannable by
            date. Bold title on the left, dates in the same row on the right.

            NOTE: these previously called set_font("Helvetica", ...) literally,
            so serif templates silently reverted to sans for every entry.
            """
            date_w = pdf.epw * 0.28 if dates else 0
            date_italic = tokens.get("dates_style") == "italic"
            pdf.set_font(font, "B", 10)

            # cell() does not wrap -- it overflows. In the wide column that
            # never showed; in a 50mm sidebar rail the degree ran straight
            # through the date and out past the margin. When the title will
            # not fit beside its dates, the row is stacked instead.
            fits = pdf.get_string_width(_latin1(primary)) <= pdf.epw - date_w - 1
            if dates and not fits:
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    pdf.epw, 5.0, _latin1(primary),
                    align="L", new_x="LMARGIN", new_y="NEXT",
                )
                pdf.set_font(font, "I" if date_italic else "", 9)
                pdf.set_x(pdf.l_margin)
                pdf.cell(pdf.epw, 4.6, _latin1(dates), new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_x(pdf.l_margin)
                pdf.cell(
                    pdf.epw - date_w, 5.5, _latin1(primary),
                    new_x="RIGHT", new_y="TOP",
                )
                if dates:
                    pdf.set_font(font, "I" if date_italic else "", 9)
                    pdf.cell(
                        date_w, 5.5, _latin1(dates),
                        new_x="LMARGIN", new_y="NEXT", align="R",
                    )
                else:
                    pdf.ln(5.5)

            if secondary:
                pdf.set_font(font, "I", 9)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    pdf.epw, 5, _latin1(secondary),
                    align="L", new_x="LMARGIN", new_y="NEXT",
                )
            pdf.set_font(font, "", 10)
        def extras_block() -> None:
            # Reuses this template's own heading()/body(), so a new section type
            # never needs a per-template branch across the seven templates.
            for section in visible_extras(data):
                heading(section["title"])
                for entry in section["entries"]:
                    body(extra_entry_line(entry))

        def sec_summary() -> None:
            text = data.get("summary", {}).get("text", "")
            if text:
                heading("Summary")
                body(text)

        def sec_experience() -> None:
            if data.get("experience"):
                heading("Experience")
                for i, entry in enumerate(data["experience"]):
                    # Air before each new employer. Without it the next role
                    # butts straight against the previous bullet and the page
                    # reads as one undifferentiated block.
                    if i:
                        pdf.ln(_ENTRY_GAP)
                    inline = tokens["entry"] == "inline"
                    # Role on top, company beneath -- the dominant layout in real
                    # templates, and easier to scan by job title. `location` was
                    # editable but rendered nowhere; experience_lines() folds it
                    # into the company so every surface shows the same thing.
                    condensed = bool(entry.get("condensed"))
                    if condensed:
                        # Older roles, one bold line with the dates and no
                        # bullets -- the reference's treatment of jobs that no
                        # longer carry the story. Uses THIS template's entry
                        # separator, so the collapsed line never clashes with
                        # the full entries around it.
                        primary, detail = experience_lines(
                            entry,
                            inline=True,
                            lead="role",
                            sep=tokens.get("entry_sep", " - "),
                        )
                    else:
                        # Role on top, company beneath -- the dominant layout in
                        # real templates, and easier to scan by job title.
                        # `location` was editable but rendered nowhere;
                        # experience_lines() folds it into the company so every
                        # surface shows the same thing.
                        primary, detail = experience_lines(
                            entry,
                            inline=inline,
                            lead=tokens.get("entry_lead", "role"),
                            sep=tokens.get("entry_sep", " - "),
                        )
                    entry_header(primary, detail, date_label(entry))

                    descriptor = (entry.get("descriptor") or "").strip()
                    if descriptor:
                        # One italic line of context about an unfamiliar
                        # employer ("Bootstrapped SaaS, 50 staff"). Deliberately
                        # NOT parsed back in: it exists only in the canonical
                        # data, not in anything a PDF can be read back as.
                        pdf.set_font(font, "I", 9)
                        pdf.set_x(pdf.l_margin)
                        pdf.multi_cell(
                            pdf.epw, 5, _latin1(descriptor),
                            align="L", new_x="LMARGIN", new_y="NEXT",
                        )
                        pdf.set_font(font, "", 10)

                    if not condensed:
                        for bullet in entry.get("bullets", []):
                            bullet_item(bullet)


        def sec_projects() -> None:
            if data.get("projects"):
                heading("Projects")
                for i, entry in enumerate(data["projects"]):
                    if i:
                        pdf.ln(_ENTRY_GAP)
                    entry_header(entry.get("name", ""), "", date_label(entry))
                    for bullet in entry.get("bullets", []):
                        bullet_item(bullet)


        def sec_education() -> None:
            if data.get("education"):
                heading("Education")
                for i, entry in enumerate(data["education"]):
                    if i:
                        pdf.ln(_ENTRY_GAP)
                    primary, detail, dates = education_cells(
                        entry,
                        inline=tokens.get("edu") == "inline",
                        school_first=tokens.get("edu_lead") == "school",
                    )
                    entry_header(primary, detail, dates)


        def sec_skills() -> None:
            if data.get("skills"):
                heading("Skills")
                # One wrapped line per group: "Languages: Java, Python, Go".
                #
                # This replaces a multi-column grid, and that grid was a standing
                # ATS hazard. Reading a three-column render back gave
                #
                #     Languages / Python Go / Java Rust / Present / 2016 / TypeScript
                #
                # -- the gutter was wide enough to trip column detection, so the
                # right-aligned entry dates were interleaved into the skills and a
                # skill was lost outright. That is why columns were clamped to two.
                # A single flowing line cannot be mis-split by a column detector,
                # so the clamp, the hazard and the skill_columns token are all gone.
                #
                # write() rather than cell()/multi_cell(): it is the only fpdf2
                # primitive that continues inline across a font change and still
                # wraps at the right margin, and the bold label and the regular
                # items have to share one flowing paragraph.
                for group in data["skills"]:
                    items = [x for x in group.get("items", []) if x]
                    if not items:
                        continue
                    label = group.get("label", "")
                    pdf.set_x(pdf.l_margin)
                    if label:
                        pdf.set_font(font, "B", 10)
                        pdf.write(line_h, _latin1(f"{label}: "))
                    pdf.set_font(font, "", 10)
                    pdf.write(line_h, _latin1(", ".join(items)))
                    pdf.ln(line_h)


        def sec_extras() -> None:
            extras_block()

        def sec_contact() -> None:
            """Contact details and links at the top of the right rail.

            This is `web-developer.png`'s header: name and headline top
            left, everything you can be reached on top right. Flush right,
            so it sits as far from the gutter as the page allows -- the
            gutter has to stay clear or the column detector loses the page.

            Each link is its own cell because fpdf2 attaches an annotation
            to a cell, not to a substring.
            """
            rows = [x for x in detail_lines if x]
            urls = [x for x in contact.get("links", []) if x]
            if not rows and not urls:
                return
            # Shrink to fit rather than wrap. A wrapped address is not a
            # cosmetic problem: "professionalemail@resumeworded.com" broke
            # across two lines in a 50mm rail and re-imported as
            # "professionalemail@resumeworded.c". An email that does not
            # survive the round trip is worse than a small one.
            for line in rows:
                size = 9.0
                while size > 6.0:
                    pdf.set_font(font, "", size)
                    if pdf.get_string_width(_latin1(line)) <= pdf.epw:
                        break
                    size -= 0.25
                pdf.set_x(pdf.l_margin)
                pdf.cell(
                    pdf.epw, 4.6, _latin1(line),
                    align="R", new_x="LMARGIN", new_y="NEXT",
                )
            pdf.set_font(font, "", 9)
            for url in urls:
                label = _latin1(link_label(url))
                size = 9.0
                while size > 6.0 and pdf.get_string_width(label) > pdf.epw:
                    size -= 0.25
                    pdf.set_font(font, "", size)
                pdf.set_x(pdf.l_margin)
                pdf.set_text_color(*_hex_to_rgb(tokens.get("accent") or "#000000"))
                pdf.cell(
                    pdf.epw, 4.6, label,
                    new_x="LMARGIN", new_y="NEXT", align="R", link=url,
                )
                pdf.set_text_color(0, 0, 0)
            pdf.ln(2.0)

        # Which sections live in the right rail of a sidebar template.
        # Experience is never one of them: it is the longest section and the
        # one a reader scans first, so it keeps the wide column. Skills sits
        # directly under Summary in EVERY template, sidebar included
        # (skills-below-summary ruling, 2026-10-25; reverses the 2026-10-24
        # "Skills below Experience AND Projects" ruling).
        MAIN = (sec_summary, sec_skills, sec_experience, sec_projects)
        # sec_contact belongs to the RAIL only. The single-column path
        # renders (*MAIN, *RAIL) in order, so listing it unconditionally
        # printed a second contact block in the middle of every other
        # template -- and the re-import read it as another employer.
        RAIL = (sec_education, sec_extras)
        if sidebar:
            RAIL = (sec_contact, *RAIL)

        if tokens.get("layout") == "sidebar":
            # Hand back the true page margin: the header above narrowed it,
            # and _render_two_columns derives both column widths from the
            # current margins. Leaving it narrowed made the helper lay the
            # rail out inside the main column, so the page rendered with no
            # right-hand text at all.
            pdf.set_right_margin(_SIDE_MARGIN)
            _render_two_columns(pdf, MAIN, RAIL)

            # Then CHECK the result, rather than predicting it.
            #
            # A two-column page is only safe if the gutter is actually
            # detectable; otherwise the page extracts interleaved and one
            # job comes back as two with the email spliced into a bullet.
            # Whether a given resume's rail is long enough is not knowable
            # from the data -- a rail at 54% of the body height passed a
            # ratio guess and still defeated the detector. So the rendered
            # bytes are run through the same detector the importer uses,
            # and anything it cannot split is re-rendered as one column.
            raw = bytes(pdf.output())
            if _gutter_is_detectable(raw):
                return raw
            return self._render_pdf(data, {**tokens, "layout": "single"})
        else:
            for section in (*MAIN, *RAIL):
                section()

        return bytes(pdf.output())

    def _render_docx(self, data: dict) -> bytes:
        import docx

        document = docx.Document()
        contact = data.get("contact", {})
        document.add_heading(contact.get("name", "Resume"), level=0)
        detail = " | ".join(
            x for x in (contact.get("email"), contact.get("phone"), contact.get("location")) if x
        )
        if detail:
            document.add_paragraph(detail)

        # DOCX dropped profile links completely, while the analysis awarded
        # +2 for having them -- the one export a recruiter is most likely to
        # open was the one that silently deleted them.
        links = [x for x in contact.get("links", []) if x]
        if links:
            para = document.add_paragraph()
            # The address, matching the PDF. A label reads well on screen
            # and is useless the moment the document is printed.
            for i, url in enumerate(links):
                if i:
                    para.add_run(" | ")
                _add_hyperlink(para, url, link_label(url))

        summary = data.get("summary", {}).get("text", "")
        if summary:
            document.add_heading("Summary", level=1)
            document.add_paragraph(summary)

        # Skills sits directly under Summary in every export format, per the
        # skills-below-summary ruling (2026-10-25), so a candidate's fit is
        # readable in the first screen of whatever file the recruiter opens.
        if data.get("skills"):
            document.add_heading("Skills", level=1)
            for group in data["skills"]:
                label = group.get("label", "")
                items = ", ".join(group.get("items", []))
                document.add_paragraph(f"{label}: {items}" if label else items)

        if data.get("experience"):
            document.add_heading("Experience", level=1)
            for entry in data["experience"]:
                head = " - ".join(
                    x
                    for x in (
                        with_location(entry.get("company", ""), entry.get("location")),
                        entry.get("role"),
                    )
                    if x
                )
                document.add_paragraph(
                    f"{head}  {entry.get('dates','')}".strip(), style="Heading 2"
                )
                descriptor = (entry.get("descriptor") or "").strip()
                if descriptor:
                    run = document.add_paragraph().add_run(descriptor)
                    run.italic = True
                if not entry.get("condensed"):
                    for bullet in entry.get("bullets", []):
                        document.add_paragraph(bullet, style="List Bullet")

        if data.get("projects"):
            document.add_heading("Projects", level=1)
            for entry in data["projects"]:
                document.add_paragraph(entry.get("name", ""), style="Heading 2")
                for bullet in entry.get("bullets", []):
                    document.add_paragraph(bullet, style="List Bullet")

        if data.get("education"):
            document.add_heading("Education", level=1)
            for entry in data["education"]:
                # DOCX export is deliberately template-free: _render_docx
                # takes no tokens argument, so it keeps the flat form.
                _edu_primary, _edu_detail = education_lines(entry)
                document.add_paragraph(
                    " — ".join(
                        x
                        for x in (
                            _edu_detail,
                            _edu_primary,
                            date_label(entry),
                        )
                        if x
                    )
                )

        for section in visible_extras(data):
            document.add_heading(section["title"], level=1)
            for entry in section["entries"]:
                document.add_paragraph(extra_entry_line(entry))

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
