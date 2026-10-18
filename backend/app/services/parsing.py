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
    _known_host,
    empty_extra_entry,
    empty_resume,
    migrate,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_LINK_RE = re.compile(r"(https?://\S+|linkedin\.com/\S+|github\.com/\S+)", re.I)
#: Separators used between fields on a contact line.
_CONTACT_SEP_RE = re.compile(r"\s*[•·|]\s*|\s{2,}|\s+[-–—]\s+")
#: Bare anchor words that stand in for a link, so they are not a location.
_LINK_WORDS = {
    "linkedin", "github", "gitlab", "portfolio", "website", "resume", "cv",
    "blog", "medium", "twitter", "x", "stack overflow", "stackoverflow",
    "leetcode", "kaggle", "behance", "dribbble", "youtube", "email", "phone",
}

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


def _both_sides_run_down_the_page(words, x: float) -> bool:
    """True only when both sides of `x` have text on most of the same rows.

    Word counts alone are not enough, and the false positive is the common
    case rather than an exotic one. Almost every professional resume flushes
    its dates to the right margin; add a two-column skills grid and the right
    side easily clears a word-count threshold, so a perfectly ordinary
    single-column page was detected as two-column and then cropped and
    re-read -- which scrambled it. Measured on our own "executive" template:
    plain extraction was flawless, and the gutter "detection" was what broke
    it, splicing the contact line into the middle of the skills list.

    A real magazine split has both columns running the length of the page.
    A right-aligned date touches three or four rows out of twenty.
    """
    rows: dict[int, list[bool]] = {}
    for w in words:
        band = int(float(w["top"]) // 6)  # ~one text line
        side = rows.setdefault(band, [False, False])
        if float(w["x1"]) <= x:
            side[0] = True
        elif float(w["x0"]) >= x:
            side[1] = True
    if len(rows) < 6:
        return False
    both = sum(1 for left, right in rows.values() if left and right)
    return both >= len(rows) * 0.5


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
            if _both_sides_run_down_the_page(words, x):
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


def _normalise_link(url: str) -> str:
    return url.strip().rstrip("/")


def extract_links(filename: str, content: bytes) -> dict[str, object]:
    """Profile URLs from a PDF's annotation layer.

    `extract_text` keeps the text layer and discards the annotations, but a
    well-made resume puts the word "LinkedIn" over a link and the URL exists
    *only* in the annotation. `_LINK_RE` scans visible text, finds nothing,
    and `contact.links.missing` then docks the user two points for profile
    links that are sitting in the file they uploaded.

    Only allowlisted hosts are returned. Some templates hyperlink every
    project title, and those belong to the project, not the contact header.

    `mailto:`/`tel:` are reported separately so the caller can backfill a
    contact field the regex missed -- they are never links.
    """
    found: list[str] = []
    email = ""
    phone = ""
    if not filename.lower().endswith(".pdf"):
        return {"links": [], "email": "", "phone": ""}
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                for link in page.hyperlinks or []:
                    uri = (link.get("uri") or "").strip()
                    if not uri:
                        continue
                    low = uri.lower()
                    if low.startswith("mailto:"):
                        email = email or uri[7:].split("?", 1)[0]
                    elif low.startswith("tel:"):
                        phone = phone or uri[4:]
                    elif low.startswith(("http://", "https://")) and _known_host(uri):
                        found.append(_normalise_link(uri))
    except Exception:  # noqa: BLE001 — links are a bonus, never a parse failure
        return {"links": [], "email": "", "phone": ""}

    seen: dict[str, str] = {}
    for url in found:
        seen.setdefault(url.lower(), url)
    return {"links": list(seen.values())[:6], "email": email, "phone": phone}


def merge_extracted_links(data: dict, extracted: dict) -> dict:
    """Fold harvested annotations into already-structured contact data.

    Anything the text layer found wins on order; annotations only add. Keeps
    `structure_text` a pure text->dict function, which twenty tests rely on.
    """
    contact = data.setdefault("contact", {})
    existing = [x for x in contact.get("links", []) if x]
    merged = list(existing)
    seen = {_normalise_link(x).lower() for x in existing}
    for url in extracted.get("links", []):
        if url.lower() not in seen:
            seen.add(url.lower())
            merged.append(url)
    contact["links"] = merged[:6]
    if not contact.get("email") and extracted.get("email"):
        contact["email"] = extracted["email"]
    if not contact.get("phone") and extracted.get("phone"):
        contact["phone"] = extracted["phone"]
    return data


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



#: Headings that look like section headings but match no known label.
#: Taleo in particular ignores content under a heading it does not
#: recognise, so "My Journey" can cost a candidate their whole work history.
_HEADINGish = re.compile(r"^[A-Z][A-Za-z &/'-]{2,38}$")


def collect_parse_facts(filename: str, content: bytes, raw_text: str) -> dict:
    """What we observed while reading the user's file.

    Facts about the UPLOAD, not about the resume's content. The scorer
    deliberately grades only content -- layout is a property of the template
    and is fixed in the exporter rather than nagged about -- but that
    reasoning covers documents WE render. It says nothing about the file the
    user handed us, which the exporter never saw and cannot repair.

    This is the missing half, and it is the direct follow-up to the parser
    bugs: two real resumes produced phantom schools and wrapped-line extras,
    and nothing anywhere told the user the reading had been shaky. These
    facts are advisory. Nothing here becomes a scored finding, so the
    finding ids and every `Δscore == points` assertion are untouched.

    Never raises. A fact we could not collect is simply absent -- failing an
    upload to compute an advisory would be an absurd trade.
    """
    facts: dict = {}
    text = raw_text or ""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    facts["format"] = suffix or "unknown"
    facts["chars"] = len(text.strip())

    if suffix == "pdf":
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as doc:
                pages = doc.pages
                facts["pages"] = len(pages)
                multi = sum(1 for pg in pages if _column_split_x(pg) is not None)
                facts["multi_column_pages"] = multi
                # An image-only (scanned) PDF has pages but no text layer.
                # No mainstream ATS runs OCR, so the recruiter receives an
                # empty profile -- the single most destructive failure here.
                facts["image_only"] = bool(pages) and facts["chars"] < 40
        except Exception as exc:  # noqa: BLE001
            logger.warning("parse facts: pdf inspection failed: %s", exc)
    elif suffix == "docx":
        try:
            import docx

            document = docx.Document(io.BytesIO(content))
            facts["tables"] = len(document.tables)
        except Exception as exc:  # noqa: BLE001
            logger.warning("parse facts: docx inspection failed: %s", exc)

    known = {label for aliases in _SECTION_HEADS.values() for label in aliases}
    unknown: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":")
        if not stripped or len(stripped.split()) > 4:
            continue
        if not _HEADINGish.match(stripped):
            continue
        if stripped.lower() in known:
            continue
        # A heading-shaped line is usually NOT a heading. "Jane Doe" and
        # "Acme Corp" match the same shape, and an early version of this
        # reported the candidate's own name as an unrecognised section --
        # advice that is both wrong and alarming.
        #
        # Two cheap signals separate a real section label from a name: it
        # is set off by blank lines on both sides, and it is not in the
        # contact block at the top of the page.
        if i < 3:
            continue
        before_blank = not lines[i - 1].strip()
        after_blank = i + 1 >= len(lines) or not lines[i + 1].strip()
        if not (before_blank and after_blank):
            continue
        unknown.append(stripped)
    facts["unknown_headings"] = unknown[:6]
    return facts


def parse_advisories(facts: dict) -> list[str]:
    """Human sentences for the facts worth telling the user about.

    Ordered worst-first. Returns [] for a clean parse, which is the common
    case -- an advisory shown on every upload is an advisory nobody reads.
    """
    if not facts:
        return []
    out: list[str] = []

    if facts.get("image_only"):
        out.append(
            "This PDF looks like a scan or an image — there is almost no "
            "selectable text in it. No mainstream ATS runs OCR, so a "
            "recruiter's system would receive an empty profile. Export a "
            "text-based PDF from your editor rather than scanning a printout."
        )
    if facts.get("multi_column_pages"):
        out.append(
            f"{facts['multi_column_pages']} page(s) use a two-column layout. "
            "We read the columns separately, but many parsers read straight "
            "across and interleave them. A single-column layout is the "
            "safest thing you can do for machine readability."
        )
    if facts.get("tables"):
        out.append(
            f"This DOCX uses {facts['tables']} table(s) for layout. We read "
            "the cells, but older parsers drop table content entirely — it is "
            "a common way for a whole skills section to vanish."
        )
    if facts.get("unknown_headings"):
        labels = ", ".join(f"\u201c{h}\u201d" for h in facts["unknown_headings"][:3])
        out.append(
            f"Unrecognised section heading(s): {labels}. Taleo and similar "
            "parsers ignore content under a heading they do not know. "
            "Standard labels (Experience, Education, Skills) are read by "
            "everything."
        )
    return out


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
#: Only real month names may introduce a year. The first branch used to be
#: ``[A-Za-z]{3,9}\.?\s?\d{4}``, which treats ANY three-to-nine letter word
#: before a year as a month: "CDAC, Pune 2024" parsed its dates as
#: "Pune 2024", and the editor then had to apologise for a date it could not
#: read. A city is not a month.
_MONTH_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_TAIL_RE = re.compile(
    r"("
    rf"{_MONTH_RE}\s?\d{{4}}.*"
    r"|(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:(?:19|20)\d{2}|present|current).*"
    r"|(?:19|20)\d{2}\s*"
    r")$",
    re.IGNORECASE,
)
_HEADER_SEP_RE = re.compile(r"\s+[-\u2013\u2014|]\s+")
#: "Score: 73%", "CGPA: 7.79" -- a property of the entry above, not an entry.
_GRADE_RE = re.compile(
    r"^(score|cgpa|gpa|percentage|percent|marks|grade|aggregate|result)\b"
    r"\s*[:\-\u2013]?\s*(.+)$",
    re.IGNORECASE,
)


def _grade_value(kind: str, value: str) -> str:
    """Keep the measure only when the number cannot speak for itself.

    "Score: 73%" -> "73%"   -- the unit already says what it is
    "CGPA: 7.79" -> "CGPA 7.79" -- a bare 7.79 on a resume is meaningless

    Dropping the label outright was the first attempt, and it turned a grade
    into an unlabelled decimal in every export.
    """
    value = value.strip().rstrip(".,;")
    if value.endswith("%"):
        return value
    return f"{kind.strip().upper() if len(kind) <= 4 else kind.strip().title()} {value}"


# A line carrying nothing but a grade VALUE, with no "Score:" / "CGPA:" label
# in front of it. Resumes very often put the number on its own line:
#
#     Post Graduate Diploma in Advanced Computing (PG-DAC) | CDAC, Pune  2024
#     73%
#
# `_GRADE_RE` needs the label, so a bare "73%" fell through to header parsing
# and became a SCHOOL CALLED "73%" -- a row that reaches the export, so a
# resume sent to an employer listed a percentage as an institution.
#
# Deliberately narrow. A bare integer is not accepted: "2024" on its own line
# is a date, and treating it as a grade would be the same bug pointing the
# other way. Only forms that cannot be anything but a grade qualify.
_BARE_GRADE_RE = re.compile(
    r"^(?:"
    r"\d{1,3}(?:\.\d+)?\s*%"          # 73%   67.85%
    r"|\d{1,2}(?:\.\d+)?\s*/\s*\d{1,2}(?:\.\d+)?"  # 7.79/10
    r")[.,;]?$"
)


def _is_only_grade(line: str) -> bool:
    """True for a line that carries nothing but an unlabelled grade."""
    return bool(_BARE_GRADE_RE.match(line.strip()))


def _merge_wrapped(lines: list[str]) -> list[str]:
    """Rejoin lines a PDF broke mid-sentence.

    The extras sections had no continuation handling at all -- every line
    became its own entry -- so

        • Founder, Kalarpan (2019-present) - Founded and run a charity
        through staged theatrical performances; own everything from ...

    produced a second "achievement" whose title was the tail of the first.

    The lowercase test is the conservative half of `_is_bullet_continuation`,
    used on its own here because extras entries have no bullet list to
    reason about. A line that opens lowercase did not start a new thought.
    """
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _BULLET_RE.match(stripped) or not out:
            out.append(line)
            continue
        previous = out[-1].rstrip()
        # Opens lowercase -- did not start a new thought.
        wrapped = bool(stripped[0].islower())
        # Or the PREVIOUS line ended mid-list. No heading has ever followed a
        # line ending in a comma, so this catches the wrap the lowercase test
        # cannot: "... Texephyr'19, Credenz'18," / "Gandharv'19, Innovation'19."
        if previous.endswith((",", ";", "-", "\u2013", "\u2014", "&", "/")):
            wrapped = True
        if wrapped:
            out[-1] = f"{previous} {stripped}"
            continue
        out.append(line)
    return out


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


_COMPANY_RE = re.compile(
    r"\b(ltd|inc|llc|llp|pvt|corp|corporation|technologies|technology|tech"
    r"|solutions|systems|labs|software|consulting|services|gmbh|plc|company"
    r"|group|industries|enterprises|holdings)\b",
    re.IGNORECASE,
)
_ROLE_RE = re.compile(
    r"\b(engineer|developer|programmer|manager|analyst|intern|designer"
    r"|consultant|architect|lead|senior|junior|principal|staff|specialist"
    r"|scientist|administrator|officer|director|associate|executive|head"
    r"|trainee|apprentice)\b",
    re.IGNORECASE,
)
_INSTITUTION_RE = re.compile(
    r"\b(university|institute|college|school|academy|polytechnic|cdac|iit"
    r"|nit|vit|iiit|bits|campus|faculty)\b",
    re.IGNORECASE,
)
_QUALIFICATION_RE = re.compile(
    r"(\bb\.?\s?e\b|\bb\.?\s?tech\b|\bm\.?\s?tech\b|\bb\.?\s?sc\b"
    r"|\bm\.?\s?sc\b|\bbca\b|\bmca\b|\bmba\b|\bdiploma\b|\bbachelor"
    r"|\bmaster|\bph\.?d\b|\bpg-?dac\b|\bxii\b|\bhsc\b|\bssc\b"
    r"|\bpost graduate\b|\bgraduate\b|\bcertificate\b)",
    re.IGNORECASE,
)


def _order_pair(
    left: str, right: str, first_re: re.Pattern, second_re: re.Pattern
) -> tuple[str, str]:
    """Order a two-sided header by evidence instead of by position.

    Headers are written both ways round and both are common:

        Acme Corp | Senior Engineer          -> company first
        Software Engineer | Ceinsys Tech Ltd -> role first

    Reading position alone put "Software Engineer" in the Company box. Each
    side is scored for how much it looks like the FIRST field minus how much
    it looks like the second; the stronger claim wins.

    **A tie keeps the given order**, so every resume that parses correctly
    today continues to -- this may only ever correct an unambiguous case.
    """
    if not right:
        return left, right

    def lean(text: str) -> int:
        return len(first_re.findall(text)) - len(second_re.findall(text))

    return (right, left) if lean(right) > lean(left) else (left, right)


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
    if indented and not _HEADER_SEP_RE.search(stripped):
        return True

    # The capital-letter rule above is not enough. A wrapped bullet very often
    # resumes on a proper noun -- "Java/Spring Boot and Python/FastAPI
    # services, ..." -- and was then read as a new employer, truncating the
    # real bullet and inventing a company from the next fragment.
    #
    # The reliable signal is not how the candidate starts but whether the
    # PREVIOUS line finished. "... integrations across" plainly did not.
    previous = entry["bullets"][-1].rstrip()
    if previous and previous[-1] not in ".!?:;":
        looks_like_header = _DATE_TAIL_RE.search(stripped) or _HEADER_SEP_RE.search(stripped)
        # Headers are short; prose that wrapped is not. Five words is
        # deliberately conservative: a false merge destroys a real entry.
        if not looks_like_header and len(stripped.split()) >= 5:
            return True
    return False



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

    # `contact.location` had no extraction at all -- not a weak one, none --
    # so `contact.location.missing` fired on every resume ever uploaded and
    # told people to add a city that was already in the header.
    #
    # The contact line is a run of fields separated by bullets, pipes or
    # slashes. Rather than guess at what a place name looks like, take the
    # field that is NOT one of the things we can positively identify: not the
    # email, not the phone, not a link, not the name, and short enough to be
    # a place rather than a sentence.
    for line in non_empty[:4]:
        if _classify(line):
            break
        if not (_EMAIL_RE.search(line) or _PHONE_RE.search(line)):
            continue
        for field in _CONTACT_SEP_RE.split(line):
            field = field.strip(" \t•|,")
            if not field or len(field) > 40 or len(field.split()) > 4:
                continue
            if _EMAIL_RE.search(field) or _PHONE_RE.search(field):
                continue
            if _LINK_RE.search(field) or field.lower() in _LINK_WORDS:
                continue
            if field == data["contact"].get("name") or any(c.isdigit() for c in field):
                continue
            data["contact"]["location"] = field
            break
        if data["contact"].get("location"):
            break

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
        # A skills GROUP LABEL is not a section heading. "Languages" is the
        # most common label on a technical resume ("Languages: Python, Go")
        # and it collides head-on with the Languages extras section, so the
        # whole skills block was being hijacked into an extras section and
        # `skills` came back empty. Inside skills, demand the evidence a real
        # heading carries -- ALL-CAPS, or a trailing colon with nothing after
        # it -- before believing a new section has started.
        if (
            section in _EXTRA_KINDS
            and current == "skills"
            and line.strip() == line.strip().title()
            and not line.strip().endswith(":")
        ):
            section = None
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
         "entries": [_extra_entry(ln) for ln in _merge_wrapped(x["lines"])]}
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

            # A PRODUCT / PROJECT SUBTITLE under the header is not a second
            # employer. Real resumes write
            #
            #     Software Engineer | Ceinsys Tech Ltd.    Apr 2024 - Present
            #     Scan to Model (S2M) - global point-cloud platform for ...
            #     - Architected a RabbitMQ-based pipeline ...
            #
            # and the subtitle's dash was read as a company/role separator,
            # so it became a new entry and -- having no bullets of its own
            # yet -- silently REPLACED the real one, taking the dates with
            # it. The tell is that the open entry has collected no bullets
            # and this line carries no dates of its own: an employer change
            # without a single bullet in between does not happen.
            stripped_line = line.strip()
            if (
                entry is not None
                and not entry["bullets"]
                and (entry["company"] or entry["role"])
                and not _split_date_tail(stripped_line)[1]
            ):
                # Keep the text -- it describes the work -- but as content,
                # not as an employer. There is no description field on an
                # entry, and discarding a line the candidate wrote is worse
                # than filing it one level down.
                entry["bullets"].append(stripped_line)
                continue

            if entry is not None and entry["bullets"]:
                entries.append(entry)
                entry = None
            header = line.strip()
            if not header:
                continue
            header, dates = _split_date_tail(header)
            parts = _HEADER_SEP_RE.split(header, maxsplit=1)
            company, role = _order_pair(
                parts[0].strip(),
                parts[1].strip() if len(parts) > 1 else "",
                _COMPANY_RE,
                _ROLE_RE,
            )
            entry = {
                "company": company,
                "role": role,
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

    # Education used to be ten lines with no entry model: every non-empty line
    # became a record, and the three fields were filled positionally. A resume
    # reading
    #
    #     Post Graduate Diploma in Advanced Computing (PG-DAC) — CDAC, Pune 2024 | Pune
    #     Score: 73%
    #
    # produced TWO entries, the second a school called "Score: 73%", with the
    # qualification in the School box and the city in the dates. Those rows
    # reach the export, so a resume sent to an employer listed a score as a
    # school.
    edu_entry: dict | None = None
    for line in buckets["education"]:
        text = line.strip()
        if not text:
            continue

        # A grade belongs to the entry above it, never to one of its own.
        grade = _GRADE_RE.match(text)
        if grade and edu_entry is not None:
            edu_entry["grade"] = _grade_value(grade.group(1), grade.group(2))
            continue
        # ... and the same line with no label in front of it.
        if edu_entry is not None and _is_only_grade(text):
            edu_entry["grade"] = text.rstrip(".,;")
            continue

        pieces: list[str] = []
        dates = ""
        for piece in _HEADER_SEP_RE.split(text):
            piece = piece.strip()
            if not piece:
                continue
            head, found = _split_date_tail(piece)
            if found and not dates:
                dates = found
            pieces.append(head.strip() or piece)

        if not pieces:
            continue

        school, degree = _order_pair(
            pieces[0],
            pieces[1] if len(pieces) > 1 else "",
            _INSTITUTION_RE,
            _QUALIFICATION_RE,
        )
        # Anything after the first two parts is a place, not a date -- the
        # dates have already been lifted out of every piece above.
        edu_entry = {
            "school": school,
            "degree": degree,
            "dates": dates,
            "location": " ".join(pieces[2:]).strip(),
            "grade": "",
        }
        data["education"].append(edu_entry)

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
        resume.parse_facts = collect_parse_facts(filename, content, raw)
        if note:
            resume.structured_data = empty_resume()
            resume.parse_status = "failed"
            resume.parse_note = note
        else:
            resume.structured_data = merge_extracted_links(
                structure_text(raw), extract_links(filename, content)
            )
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
            resume.parse_facts = collect_parse_facts(filename, content, raw)
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
                resume.structured_data = merge_extracted_links(
                    structure_text(raw), extract_links(filename, content)
                )
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
