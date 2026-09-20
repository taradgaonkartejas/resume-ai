import io
import uuid

from app.config import settings
from app.repositories.resume_repository import ResumeRepository
from app.services import storage
from app.services.exceptions import ResumeNotFound, UnsupportedFormat
from app.services.resume_ops import to_plain_text

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


def _latin1(text: str) -> str:
    """fpdf2 core fonts are Latin-1 only; drop what cannot be encoded."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


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


class ExportService:
    """Renders PDF / DOCX / TXT. Results are cached per resume version."""

    def __init__(self, resumes: ResumeRepository) -> None:
        self.resumes = resumes

    def export(self, resume_id: uuid.UUID, user_id: uuid.UUID, fmt: str) -> tuple[bytes, str, str]:
        fmt = fmt.lower()
        if fmt not in FORMATS:
            raise UnsupportedFormat(f"{fmt}: expected pdf, docx or txt")
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))

        key = f"{user_id}/{resume_id}/v{resume.version_cursor}.{fmt}"
        filename = _safe_filename(resume.title, fmt)

        if storage.object_exists(settings.s3_bucket_exports, key):
            return storage.get_object(settings.s3_bucket_exports, key), FORMATS[fmt], filename

        data = resume.structured_data or {}
        if fmt == "txt":
            payload = to_plain_text(data).encode("utf-8")
        elif fmt == "pdf":
            payload = self._render_pdf(data)
        else:
            payload = self._render_docx(data)

        storage.put_object(settings.s3_bucket_exports, key, payload, FORMATS[fmt])
        return payload, FORMATS[fmt], filename

    def _render_pdf(self, data: dict) -> bytes:
        from fpdf import FPDF

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
            pdf.multi_cell(pdf.epw, 5, _latin1(text))

        contact = data.get("contact", {})
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 9, _latin1(contact.get("name", "Resume")), new_x="LMARGIN", new_y="NEXT")

        detail = " | ".join(
            x for x in (contact.get("email"), contact.get("phone"), contact.get("location")) if x
        )
        if detail:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, _latin1(detail), new_x="LMARGIN", new_y="NEXT")

        def heading(title: str) -> None:
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 7, _latin1(title.upper()), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)

        summary = data.get("summary", {}).get("text", "")
        if summary:
            heading("Summary")
            body(summary)

        if data.get("experience"):
            heading("Experience")
            for entry in data["experience"]:
                pdf.set_font("Helvetica", "B", 10)
                head = " - ".join(x for x in (entry.get("company"), entry.get("role")) if x)
                pdf.set_x(pdf.l_margin)
                pdf.cell(pdf.epw, 6, _latin1(f"{head}  {entry.get('dates','')}".strip()),
                         new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                for bullet in entry.get("bullets", []):
                    body(f"  - {bullet}")

        if data.get("projects"):
            heading("Projects")
            for entry in data["projects"]:
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, _latin1(entry.get("name", "")), new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                for bullet in entry.get("bullets", []):
                    body(f"  - {bullet}")

        if data.get("education"):
            heading("Education")
            for entry in data["education"]:
                line = " - ".join(
                    x for x in (entry.get("school"), entry.get("degree"), entry.get("dates")) if x
                )
                body(line)

        if data.get("skills"):
            heading("Skills")
            for group in data["skills"]:
                label = group.get("label", "")
                items = ", ".join(group.get("items", []))
                body(f"{label}: {items}" if label else items)

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

        summary = data.get("summary", {}).get("text", "")
        if summary:
            document.add_heading("Summary", level=1)
            document.add_paragraph(summary)

        if data.get("experience"):
            document.add_heading("Experience", level=1)
            for entry in data["experience"]:
                head = " - ".join(
                    x for x in (entry.get("company"), entry.get("role")) if x
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
                document.add_paragraph(
                    " — ".join(
                        x
                        for x in (
                            entry.get("school"),
                            entry.get("degree"),
                            entry.get("dates"),
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

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
