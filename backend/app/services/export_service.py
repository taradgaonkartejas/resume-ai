import io
import uuid

from app.config import settings
from app.repositories.resume_repository import ResumeRepository
from app.repositories.template_repository import TemplateRepository
from app.services import storage
from app.services.exceptions import ResumeNotFound, UnsupportedFormat
from app.services.resume_ops import (
    date_label,
    education_lines,
    experience_lines,
    extra_entry_line,
    label_links,
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
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2012": "-",  # dashes
    "\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201b": "'",  # single quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',  # double quotes
    "\u2026": "...", "\u2022": "-", "\u00b7": "-", "\u2023": "-",
    "\u2010": "-", "\u2011": "-", "\u00a0": " ", "\u202f": " ",
    "\u2009": " ", "\u200b": "", "\ufeff": "",
    "\u2192": "->", "\u2190": "<-", "\u2264": "<=", "\u2265": ">=",
    "\u00d7": "x", "\u2122": "(TM)", "\u2033": '"', "\u2032": "'",
}

_TRANSLITERATE_TABLE = str.maketrans(_TRANSLITERATE)


def _latin1(text: str) -> str:
    """Make text safe for fpdf2's core fonts, which are Latin-1 only.

    Transliterate first, replace only as a last resort. The previous version
    went straight to errors="replace", so an en-dash or a curly quote became a
    literal "?" in the exported PDF.
    """
    return (
        text.translate(_TRANSLITERATE_TABLE)
        .encode("latin-1", errors="replace")
        .decode("latin-1")
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
    # Skills laid out in N columns, as printed templates do.
    "skill_columns": 1,
}

# fpdf2 core fonts. "serif" maps to Times, "sans" to Helvetica -- both are
# built in, so no font files ship with the app.
_PDF_FONTS = {"sans": "Helvetica", "serif": "Times"}

# density -> (line height, gap before a heading)
_DENSITY = {"dense": (4.4, 2.0), "normal": (5.0, 3.0), "airy": (5.8, 4.5)}


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
        font = _PDF_FONTS.get(tokens["font"], "Helvetica")
        line_h, head_gap = _DENSITY.get(tokens["density"], _DENSITY["normal"])
        accent = _hex_to_rgb(tokens["accent"])
        centred = tokens["name_align"] == "center"
        style = tokens["heading"]

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        def body(text: str) -> None:
            """Always reset x first.

            cell() leaves the cursor at the right margin; a following
            multi_cell(0, ...) then has zero available width and fpdf2 raises
            "Not enough horizontal space to render a single character".
            """
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.epw, line_h, _latin1(text))

        contact = data.get("contact", {})
        name = contact.get("name", "Resume")
        headline = contact.get("headline", "")
        bits = [contact.get("email"), contact.get("phone"), contact.get("location")]
        detail_lines = [x for x in bits if x]
        align = "C" if centred else "L"

        if tokens["header"] == "split" and not centred:
            # Name and headline left, contact details right-aligned BELOW
            # them -- not beside them.
            #
            # This used to be a true two-column header: name on the left,
            # contact stacked on the right, both starting at the same y. It
            # looked right and extracted as this:
            #
            #     Jane Doe jane@example.com
            #     +91 8208300420
            #     Senior Engineer Pune, India
            #
            # A PDF text extractor reads straight across the page, so the two
            # columns interleave -- the name fuses to the email and the job
            # title to the city. That is the single most common ATS parse
            # failure (~34% of field errors) landing on the one block whose
            # corruption means a recruiter cannot make contact at all.
            #
            # There is no clever fix: anything genuinely side by side shares
            # a text line. The layout stays visually distinct from "stacked"
            # by right-aligning the detail row instead.
            pdf.set_font(font, "B", 20 if tokens["density"] == "airy" else 16)
            if accent:
                pdf.set_text_color(*accent)
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 9, _latin1(name), new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.set_text_color(0, 0, 0)
            if headline:
                pdf.set_font(font, "", 11)
                pdf.set_x(pdf.l_margin)
                pdf.cell(0, 6, _latin1(headline), new_x="LMARGIN", new_y="NEXT", align="L")

            if detail_lines:
                pdf.set_font(font, "", 9)
                pdf.set_x(pdf.l_margin)
                pdf.cell(
                    0, 5, _latin1("  |  ".join(detail_lines)),
                    new_x="LMARGIN", new_y="NEXT", align="R",
                )
        else:
            pdf.set_font(font, "B", 20 if tokens["density"] == "airy" else 16)
            if accent:
                pdf.set_text_color(*accent)
            pdf.cell(0, 9, _latin1(name), new_x="LMARGIN", new_y="NEXT", align=align)
            pdf.set_text_color(0, 0, 0)

            if headline:
                pdf.set_font(font, "", 11)
                pdf.cell(0, 6, _latin1(headline), new_x="LMARGIN", new_y="NEXT", align=align)

            detail = "  |  ".join(detail_lines)
            if detail:
                pdf.set_font(font, "", 9)
                pdf.cell(0, 6, _latin1(detail), new_x="LMARGIN", new_y="NEXT", align=align)

        links = [x for x in contact.get("links", []) if x]
        if links:
            # Labels, not raw URLs, and each one a real clickable annotation.
            # A 54-character LinkedIn URL in a contact header is noise that
            # nobody retypes; the resume this feature was built for already
            # writes "LinkedIn" over a hyperlink, so the export matches it.
            #
            # One cell per label, because fpdf2 attaches a link to a cell,
            # not to a substring. That means laying the row out by hand:
            # measure it, then set x so the result still honours the
            # template's alignment.
            pdf.set_font(font, "", 9)
            pairs = label_links(links)
            sep = "  |  "
            sep_w = pdf.get_string_width(sep)
            widths = [pdf.get_string_width(label) for _, label in pairs]
            total = sum(widths) + sep_w * (len(pairs) - 1)
            if align == "C":
                start = pdf.l_margin + (pdf.epw - total) / 2
            elif align == "R":
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

        summary = data.get("summary", {}).get("text", "")
        if summary:
            heading("Summary")
            body(summary)

        def entry_header(primary: str, secondary: str, dates: str) -> None:
            """Title row with the dates flushed right.

            Printed templates put dates hard against the right margin rather
            than trailing the title, which is what makes a resume scannable by
            date. Bold title on the left, dates in the same row on the right.

            NOTE: these previously called set_font("Helvetica", ...) literally,
            so serif templates silently reverted to sans for every entry.
            """
            date_w = pdf.epw * 0.28 if dates else 0
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font, "B", 10)
            pdf.cell(pdf.epw - date_w, 5.5, _latin1(primary), new_x="RIGHT", new_y="TOP")
            if dates:
                pdf.set_font(font, "", 9)
                pdf.cell(date_w, 5.5, _latin1(dates), new_x="LMARGIN", new_y="NEXT", align="R")
            else:
                pdf.ln(5.5)

            if secondary:
                pdf.set_font(font, "I", 9)
                pdf.set_x(pdf.l_margin)
                pdf.cell(pdf.epw, 5, _latin1(secondary), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(font, "", 10)

        if data.get("experience"):
            heading("Experience")
            for entry in data["experience"]:
                inline = tokens["entry"] == "inline"
                # Role on top, company beneath -- the dominant layout in real
                # templates, and easier to scan by job title. `location` was
                # editable but rendered nowhere; experience_lines() folds it
                # into the company so every surface shows the same thing.
                primary, detail = experience_lines(entry, inline=inline)
                entry_header(primary, detail, date_label(entry))
                for bullet in entry.get("bullets", []):
                    body(f"  - {bullet}")

        if data.get("projects"):
            heading("Projects")
            for entry in data["projects"]:
                entry_header(entry.get("name", ""), "", date_label(entry))
                for bullet in entry.get("bullets", []):
                    body(f"  - {bullet}")

        if data.get("education"):
            heading("Education")
            for entry in data["education"]:
                primary, detail = education_lines(entry)
                entry_header(primary, detail, date_label(entry))

        def extras_block() -> None:
            # Reuses this template's own heading()/body(), so a new section type
            # never needs a per-template branch across the seven templates.
            for section in visible_extras(data):
                heading(section["title"])
                for entry in section["entries"]:
                    body(extra_entry_line(entry))

        if data.get("skills"):
            heading("Skills")
            # Capped at TWO, not three, on measured evidence rather than
            # taste: rendering the same resume at three columns and reading
            # the text back produced
            #
            #     Languages / Python Go / Java Rust / Present / 2016 / TypeScript
            #
            # -- the grid opens a gutter wide enough to trip column
            # detection, so the right-aligned entry dates get interleaved
            # into the skills list and one skill is lost outright. Two
            # columns round-trip cleanly on every template. A template
            # asking for three is clamped rather than refused, so no
            # existing template key breaks.
            columns = max(1, min(int(tokens.get("skill_columns", 1) or 1), 2))
            for group in data["skills"]:
                label = group.get("label", "")
                items = [x for x in group.get("items", []) if x]
                if not items:
                    continue
                if label:
                    pdf.set_font(font, "B", 9.5)
                    pdf.set_x(pdf.l_margin)
                    pdf.cell(pdf.epw, 5, _latin1(label), new_x="LMARGIN", new_y="NEXT")
                    pdf.set_font(font, "", 10)

                if columns == 1:
                    body(", ".join(items))
                    continue

                # Fill down each column, as printed skill grids do: fpdf2 has
                # no flow layout, so rows are composed explicitly.
                #
                # Separator is a COMMA, not "- ". A PDF text extractor reads
                # straight across the page, so a grid comes back row-wise:
                # with dashes that was "- Python - Go - TypeScript", which a
                # parser tokenises as junk. With commas the very same row
                # extracts as "Python, Go, TypeScript" -- a valid skills line.
                # The grid still looks like a grid; only the text layer
                # changes. Column order is lost in extraction either way, and
                # for a skills list order carries no meaning.
                col_w = pdf.epw / columns
                rows = -(-len(items) // columns)  # ceil
                for r in range(rows):
                    pdf.set_x(pdf.l_margin)
                    row_last = max(
                        (c for c in range(columns) if c * rows + r < len(items)),
                        default=-1,
                    )
                    for c in range(columns):
                        idx = c * rows + r
                        if idx < len(items):
                            text = items[idx] + ("," if c < row_last else "")
                        else:
                            text = ""
                        last = c == columns - 1
                        pdf.cell(
                            col_w,
                            line_h,
                            _latin1(text),
                            new_x="LMARGIN" if last else "RIGHT",
                            new_y="NEXT" if last else "TOP",
                        )

        extras_block()

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
            for i, (url, label) in enumerate(label_links(links)):
                if i:
                    para.add_run(" | ")
                _add_hyperlink(para, url, label)

        summary = data.get("summary", {}).get("text", "")
        if summary:
            document.add_heading("Summary", level=1)
            document.add_paragraph(summary)

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

        if data.get("skills"):
            document.add_heading("Skills", level=1)
            for group in data["skills"]:
                label = group.get("label", "")
                items = ", ".join(group.get("items", []))
                document.add_paragraph(f"{label}: {items}" if label else items)

        for section in visible_extras(data):
            document.add_heading(section["title"], level=1)
            for entry in section["entries"]:
                document.add_paragraph(extra_entry_line(entry))

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
