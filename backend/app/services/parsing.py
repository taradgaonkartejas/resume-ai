"""Upload parsing: bytes -> raw text -> structured resume JSON.

Regex-based and fully offline. When an LLM key is present the extraction agent
can refine the result later; the rule path must always produce something usable.
"""

import io
import logging
import re

from app.config import settings
from app.models import Resume
from app.repositories.resume_repository import ResumeRepository
from app.repositories.vector_repository import VectorRepository
from app.services import storage
from app.services.exceptions import ResumeNotFound, ValidationError
from app.services.resume_ops import (
    EXTRA_PRESETS,
    empty_extra_entry,
    empty_resume,
    migrate,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_LINK_RE = re.compile(r"(https?://\S+|linkedin\.com/\S+|github\.com/\S+)", re.I)

_SECTION_HEADS = {
    "summary": {"summary", "profile", "objective", "about"},
    "experience": {"experience", "employment", "work history", "professional experience"},
    "projects": {"projects", "personal projects", "selected projects"},
    "education": {"education", "academics"},
    "skills": {"skills", "technical skills", "technologies"},
    # --- optional sections, routed to data["extras"] rather than a bucket ---
    "certifications": {"certifications", "certificates", "licenses", "licences",
                       "certifications & licenses", "certifications and licenses"},
    "languages": {"languages", "language proficiency", "languages known"},
    "awards": {"awards", "honors", "honours", "achievements", "awards & honors",
               "awards and honors", "honors & awards"},
    "publications": {"publications", "papers", "research", "research papers"},
    "references": {"references", "referees"},
}

# Which of the above are extras rather than first-class resume sections.
_EXTRA_KINDS = ("certifications", "languages", "awards", "publications", "references")

# Glyph bullets plus numbered lists ("1." / "2)"). Without the numeric
# alternative a numbered resume produced ZERO experience entries: the lines
# fell through to the header branch and were consumed as company/role.
_BULLET_RE = re.compile(r"^\s*(?:[-•*\u2022\u25cf\u25aa]|\d+[.)])\s+(.*)$")

# Extraction-failure thresholds. These diagnose "we could not read the file",
# which is NOT the same question as "is this resume any good".
#
# The distinction is format-specific on purpose: a PDF can be a page of scanned
# pixels that yields no text operators, so a PDF with almost no words is
# overwhelmingly a scan. A .txt or .docx cannot fail that way -- if it holds
# four words then it genuinely holds four words, and telling that user their
# file "may be a scanned image" would be wrong.
MIN_WORDS_PDF = 15
MIN_WORDS_TEXT = 3
_DATE_RE = re.compile(
    r"((19|20)\d{2}|present|current|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)


# Columns narrower than this fraction of the page are not a real gutter.
_MIN_GUTTER = 0.04
# Both sides of a gutter must carry at least this many words for it to count.
_MIN_COLUMN_WORDS = 4


def _column_split_x(page) -> float | None:
    """Find the x of a two-column gutter, or None for a single-column page.

    A gutter is a vertical band that no word crosses, with substantial text on
    both sides. Scanning candidate x positions and keeping the widest such band
    is enough; we are detecting a magazine-style split, not doing layout
    analysis.
    """
    words = page.extract_words() or []
    if len(words) < _MIN_COLUMN_WORDS * 2:
        return None
    spans = [(float(w["x0"]), float(w["x1"])) for w in words]
    width = float(page.width) or 1.0

    clear: list[float] = []
    for pct in range(25, 76):
        x = width * pct / 100.0
        if any(a < x < b for a, b in spans):
            continue
        left = sum(1 for a, b in spans if b <= x)
        right = sum(1 for a, b in spans if a >= x)
        if left >= _MIN_COLUMN_WORDS and right >= _MIN_COLUMN_WORDS:
            clear.append(x)
    if not clear:
        return None

    # Widest contiguous run of clear positions.
    step = width / 100.0
    best_run: list[float] = []
    run: list[float] = [clear[0]]
    for x in clear[1:]:
        if x - run[-1] <= step * 1.5:
            run.append(x)
        else:
            best_run = max(best_run, run, key=len)
            run = [x]
    best_run = max(best_run, run, key=len)

    if (best_run[-1] - best_run[0]) < width * _MIN_GUTTER:
        return None
    return (best_run[0] + best_run[-1]) / 2.0


def _extract_pdf(content: bytes) -> str:
    """Text from a PDF, reading each column in order on two-column pages.

    pypdf emits a raw per-page text stream with no notion of reading order, so
    a two-column resume comes out interleaved row by row:

        EXPERIENCE / SKILLS / Acme Corp - Senior Engineer / Kubernetes, Docker

    which the segmenter then reads as one nonsensical document. pdfplumber's
    default extract_text() is no better -- it joins the columns on each line.
    Detecting the gutter and cropping to it is what actually fixes the reading
    order. Single-column pages take the ordinary path.
    """
    try:
        import pdfplumber

        out: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as doc:
            for page in doc.pages:
                split = _column_split_x(page)
                if split is None:
                    out.append(page.extract_text() or "")
                    continue
                left = page.crop((0, 0, split, page.height)).extract_text() or ""
                right = page.crop((split, 0, page.width, page.height)).extract_text() or ""
                out.append("\n".join(part for part in (left, right) if part.strip()))
        text = "\n".join(out)
        if text.strip():
            return text
    except Exception as exc:  # noqa: BLE001 -- never lose a file to a parser bug
        logger.warning("pdfplumber extraction failed, falling back to pypdf: %s", exc)

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)



def extract_text(filename: str, content: bytes) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "pdf":
        return _extract_pdf(content)
    if suffix == "docx":
        import docx

        document = docx.Document(io.BytesIO(content))
        parts = [p.text for p in document.paragraphs]
        # document.paragraphs EXCLUDES table cells. Resumes routinely put the
        # whole skills grid (or a two-column contact block) in a table, and all
        # of it was being dropped silently.
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                # A merged cell repeats its text across the row; de-duplicate
                # consecutively so "Python, Go" does not appear three times.
                deduped = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
                if deduped:
                    parts.append(": ".join(deduped) if len(deduped) > 1 else deduped[0])
        return "\n".join(parts)
    return content.decode("utf-8", errors="replace")


# A trailing date range on a header line. Three shapes, because resumes use all
# three: "Jan 2020 - Present", a purely numeric "2020-2024", and a lone
# trailing year. The original pattern only had the first, so a header written
# "Acme Corp - Engineer - 2020-2024" kept the dates glued to the role.
_DATE_TAIL_RE = re.compile(
    r"("
    r"[A-Za-z]{3,9}\.?\s?\d{4}.*"
    r"|(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:(?:19|20)\d{2}|present|current).*"
    r"|(?:19|20)\d{2}\s*"
    r")$",
    re.IGNORECASE,
)
_HEADER_SEP_RE = re.compile(r"\s+[-\u2013\u2014|]\s+")


def _split_date_tail(header: str) -> tuple[str, str]:
    """Split a header into (remainder, dates). Dates is "" when absent."""
    match = _DATE_TAIL_RE.search(header)
    if not match or not _DATE_RE.search(match.group(1)):
        return header, ""
    return header[: match.start()].strip(" -\u2013\u2014|"), match.group(1).strip()


def _is_only_dates(line: str) -> bool:
    """True for a line that carries nothing but a date range.

    Resumes very often put dates on their own line under the employer. Such a
    line used to fall through to header parsing and overwrite the company and
    role that had just been read.
    """
    stripped = line.strip()
    if not stripped:
        return False
    remainder, dates = _split_date_tail(stripped)
    return bool(dates) and not remainder


def _is_bullet_continuation(line: str, entry: dict | None) -> bool:
    """True for a wrapped bullet that lost its glyph.

    PDF-to-text routinely breaks a long bullet across lines without repeating
    the marker. Such a line used to close the entry and be re-read as a new
    employer, truncating the real bullet and fabricating a company called
    something like "deploy time by 60% and improving reliability".

    Deliberately conservative: a false merge destroys a real entry, so we only
    merge on strong evidence -- the line starts lowercase, or it is indented
    and carries no header-ish structure.
    """
    if entry is None or not entry.get("bullets"):
        return False
    stripped = line.strip()
    if not stripped or _is_only_dates(stripped):
        return False
    if _DATE_TAIL_RE.search(stripped) and _DATE_RE.search(stripped):
        return False  # looks like a dated header, not prose
    if stripped[0].islower():
        return True
    indented = line[:1] in (" ", "\t")
    return indented and not _HEADER_SEP_RE.search(stripped)



def _classify(line: str) -> str | None:
    key = line.strip().lower().rstrip(":")
    if len(key) > 40:
        return None
    for section, aliases in _SECTION_HEADS.items():
        if key in aliases:
            return section
    return None


# Split an extras line into primary/secondary on the FIRST separator only.
# Never guess past one: a wrong field assignment is worse than an unsplit line,
# because the user can see and fix the second but may not notice the first.
_EXTRA_SPLIT_RE = re.compile(r"\s*[|\u2013\u2014]\s*|\s+-\s+|,\s+")
_YEAR_TAIL_RE = re.compile(
    r"[\s,(\u2013\u2014-]+((?:19|20)\d{2}"
    r"(?:\s*[-\u2013\u2014]\s*(?:present|current|(?:19|20)\d{2}))?)\)?\s*$",
    re.I,
)


def _custom_heading(line: str) -> str | None:
    """A heading we do not recognise, e.g. "VOLUNTEERING" or "Patents:".

    Deliberately conservative: **ALL-CAPS or a trailing colon only**. Plain
    Title Case is rejected because it is indistinguishable from a company name
    ("Acme Corporation"), and promoting one of those to a section would eat the
    experience entry beneath it. A missed custom section costs the user a manual
    re-add; a false positive silently corrupts a resume that parses fine today.
    """
    stripped = line.strip()
    if not stripped or _BULLET_RE.match(line):
        return None

    had_colon = stripped.endswith(":")
    text = stripped.rstrip(":").strip()
    if not (1 <= len(text.split()) <= 5) or len(text) > 40:
        return None
    if text.endswith("."):
        return None
    # Contact details, dates and anything numeric are never section headings.
    if "@" in text or _LINK_RE.search(text) or _PHONE_RE.search(text):
        return None
    if any(ch.isdigit() for ch in text):
        return None
    if not any(ch.isalpha() for ch in text):
        return None

    if text.isupper():
        return text.title()
    if had_colon:
        return text
    return None


def _extra_entry(line: str) -> dict:
    """One extras line -> the four-field entry shape."""
    bullet = _BULLET_RE.match(line)
    text = (bullet.group(1) if bullet else line).strip()

    entry = empty_extra_entry()
    year = _YEAR_TAIL_RE.search(text)
    if year:
        entry["date"] = year.group(1).strip()
        text = text[: year.start()].strip().rstrip(",-\u2013\u2014|").strip()

    parts = _EXTRA_SPLIT_RE.split(text, maxsplit=1)
    entry["primary"] = parts[0].strip()
    if len(parts) > 1:
        entry["secondary"] = parts[1].strip()
    return entry


def structure_text(raw: str) -> dict:
    data = empty_resume()
    lines = [ln.rstrip() for ln in raw.splitlines()]
    non_empty = [ln for ln in lines if ln.strip()]

    if non_empty:
        first = non_empty[0].strip()
        if len(first) < 60 and "@" not in first:
            data["contact"]["name"] = first

            # The line straight after the name is the professional title on
            # essentially every resume layout ("Dental Office Manager").
            # Only accept it when it looks like a title rather than contact
            # data or a section heading, so we never promote "email | phone"
            # into the headline.
            if len(non_empty) > 1:
                second = non_empty[1].strip()
                looks_like_contact = (
                    "@" in second
                    or _PHONE_RE.search(second)
                    or _LINK_RE.search(second)
                    or "|" in second
                )
                if (
                    2 < len(second) <= 60
                    and not looks_like_contact
                    and _classify(second) is None
                    and not _BULLET_RE.match(second)
                ):
                    data["contact"]["headline"] = second

    email = _EMAIL_RE.search(raw)
    if email:
        data["contact"]["email"] = email.group(0)
    phone = _PHONE_RE.search(raw)
    if phone:
        data["contact"]["phone"] = phone.group(0).strip()
    data["contact"]["links"] = list(dict.fromkeys(_LINK_RE.findall(raw)))[:4]

    buckets: dict[str, list[str]] = {
        k: [] for k in _SECTION_HEADS if k not in _EXTRA_KINDS
    }
    extras_raw: list[dict] = []
    current: str | None = None
    current_extra: dict | None = None
    # Custom-heading detection is armed only AFTER a recognised heading has been
    # seen. Without this the name and headline at the top of every resume -- short,
    # capitalised, no digits -- would be promoted to sections.
    seen_known_heading = False

    for line in lines:
        section = _classify(line)
        if section:
            seen_known_heading = True
            if section in _EXTRA_KINDS:
                current_extra = {
                    "kind": section,
                    "title": EXTRA_PRESETS[section]["title"],
                    "lines": [],
                }
                extras_raw.append(current_extra)
                current = None
            else:
                current, current_extra = section, None
            continue

        if seen_known_heading:
            title = _custom_heading(line)
            if title:
                current_extra = {"kind": "custom", "title": title, "lines": []}
                extras_raw.append(current_extra)
                current = None
                continue

        if line.strip():
            if current_extra is not None:
                current_extra["lines"].append(line)
            elif current:
                buckets[current].append(line)

    # A heading with nothing under it is dropped -- that is the "requires >=1
    # content line" guard, applied after the fact so it also catches a trailing
    # heading at end of file.
    data["extras"] = [
        {"kind": x["kind"], "title": x["title"],
         "entries": [_extra_entry(ln) for ln in x["lines"]]}
        for x in extras_raw if x["lines"]
    ]

    data["summary"]["text"] = " ".join(buckets["summary"]).strip()

    for key in ("experience", "projects"):
        entries: list[dict] = []
        entry: dict | None = None
        for line in buckets[key]:
            bullet = _BULLET_RE.match(line)
            if bullet:
                if entry is None:
                    entry = {"company": "", "role": "", "dates": "", "bullets": []}
                entry["bullets"].append(bullet.group(1).strip())
                continue
            # A date-only line belongs to the entry we just opened, not to a
            # new one. Without this the company and role were silently wiped.
            if entry is not None and not entry["dates"] and _is_only_dates(line):
                entry["dates"] = _split_date_tail(line.strip())[1]
                continue

            # A wrapped bullet rejoins the bullet it came from.
            if _is_bullet_continuation(line, entry):
                entry["bullets"][-1] = f"{entry['bullets'][-1]} {line.strip()}"
                continue

            if entry is not None and entry["bullets"]:
                entries.append(entry)
                entry = None
            header = line.strip()
            if not header:
                continue
            header, dates = _split_date_tail(header)
            parts = _HEADER_SEP_RE.split(header, maxsplit=1)
            entry = {
                "company": parts[0].strip(),
                "role": parts[1].strip() if len(parts) > 1 else "",
                "dates": dates,
                "bullets": [],
            }
        if entry is not None and entry["bullets"]:
            entries.append(entry)
        if key == "projects":
            for e in entries:
                e["name"] = e.pop("company", "")
                e.pop("role", None)
        data[key] = entries

    for line in buckets["education"]:
        text = line.strip()
        if not text:
            continue
        parts = re.split(r"\s+[-–—|]\s+", text)
        data["education"].append(
            {
                "school": parts[0].strip(),
                "degree": parts[1].strip() if len(parts) > 1 else "",
                "dates": parts[2].strip() if len(parts) > 2 else "",
            }
        )

    for line in buckets["skills"]:
        text = line.strip().lstrip("-•* ")
        if not text:
            continue
        if ":" in text:
            label, items = text.split(":", 1)
        else:
            label, items = "", text
        values = [s.strip() for s in re.split(r"[,;|]", items) if s.strip()]
        if values:
            data["skills"].append({"label": label.strip(), "items": values})

    # Uploads land already-migrated, so a parsed resume and a hand-edited one
    # are the same shape from the first byte.
    return migrate(data)


def _extraction_failure(filename: str, raw: str) -> str:
    """Return a user-facing reason if extraction clearly failed, else ""."""
    words = len(raw.split())
    is_pdf = filename.lower().endswith(".pdf")
    if is_pdf and words < MIN_WORDS_PDF:
        return (
            "No readable text found - this file may be a scanned image. "
            "Please upload a text-based PDF or DOCX."
        )
    if words < MIN_WORDS_TEXT:
        return "This file appears to be empty - no readable text was found."
    return ""



class ParsingService:
    def __init__(
        self,
        resumes: ResumeRepository,
        vectors: VectorRepository | None = None,
        versions=None,
    ) -> None:
        self.resumes = resumes
        # Optional so existing construction sites keep working.
        self.vectors = vectors
        self.versions = versions

    def reparse(self, resume_id, user_id) -> Resume:
        """Re-run extraction against the originally uploaded file.

        Parser fixes do not reach resumes that were already parsed --
        structured_data is written once at upload and then owned by the user.
        Without this, everyone who uploaded before a segmentation fix keeps the
        bad parse (a fabricated employer, a wiped company) until they think to
        re-upload, which they never will because nothing tells them to.

        This deliberately DISCARDS manual edits, because it re-derives the
        document from the file. It records a version first, so the edits are
        one undo away rather than gone.
        """
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        if not (resume.storage_key or "").strip():
            raise ValidationError(
                "This resume was created in the editor, not uploaded, so there "
                "is no original file to re-read."
            )

        if self.versions is not None and resume.structured_data:
            self.versions.record(
                resume,
                resume.structured_data,
                change_source="reparse",
                label="Before re-reading the uploaded file",
            )

        content = storage.get_object(settings.s3_bucket_uploads, resume.storage_key)
        filename = resume.storage_key.rsplit("/", 1)[-1]
        raw = extract_text(filename, content)
        note = _extraction_failure(filename, raw)
        resume.raw_text = raw
        if note:
            resume.structured_data = empty_resume()
            resume.parse_status = "failed"
            resume.parse_note = note
        else:
            resume.structured_data = structure_text(raw)
            resume.parse_status = "ready"
            resume.parse_note = ""
        self.resumes.db.commit()

        if resume.parse_status == "ready":
            self._index(resume)
            self.resumes.db.commit()
        return resume


    def parse_resume(self, resume_id, user_id) -> Resume:
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        try:
            content = storage.get_object(settings.s3_bucket_uploads, resume.storage_key)
            filename = resume.storage_key.rsplit("/", 1)[-1]
            raw = extract_text(filename, content)
            resume.raw_text = raw
            # pypdf returns "" for image-only pages and raises nothing, so a
            # scanned resume used to land as parse_status="ready" holding an
            # empty document. The user then saw a near-zero ATS score and no
            # hint that the real problem was "we could not read your file".
            note = _extraction_failure(filename, raw)
            if note:
                resume.structured_data = empty_resume()
                resume.parse_status = "failed"
                resume.parse_note = note
            else:
                resume.structured_data = structure_text(raw)
                resume.parse_status = "ready"
                resume.parse_note = ""
        except Exception as exc:  # noqa: BLE001 — surfaced via parse_status
            resume.parse_status = "failed"
            resume.parse_note = f"{type(exc).__name__}: {exc}"
        self.resumes.db.commit()

        # Index AFTER the commit: uploaded resumes were previously never
        # indexed at all (only the seeder called index_resume), so the writer
        # agent had no grounding bullets to retrieve for anything the user
        # actually uploaded.
        if resume.parse_status == "ready":
            self._index(resume)
            self.resumes.db.commit()
        return resume

    def _index(self, resume: Resume) -> int:
        """Never fatal: a parsed resume the user can edit and export beats a
        failed upload because an embedding call timed out."""
        if self.vectors is None:
            return 0
        try:
            from app.ai.vectorstore import VectorStore

            return VectorStore(self.vectors).index_resume(
                resume.user_id, resume.id, resume.structured_data or {}
            )
        except Exception as exc:  # noqa: BLE001 — degraded retrieval, not a failed upload
            logger.warning("Indexing resume %s failed: %s", resume.id, exc)
            return 0
