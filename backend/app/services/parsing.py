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
from app.services.exceptions import ResumeNotFound
from app.services.resume_ops import empty_resume, migrate

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
}

_BULLET_RE = re.compile(r"^\s*[-•*\u2022\u25cf\u25aa]\s+(.*)$")
_DATE_RE = re.compile(
    r"((19|20)\d{2}|present|current|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)


def extract_text(filename: str, content: bytes) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix == "docx":
        import docx

        document = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in document.paragraphs)
    return content.decode("utf-8", errors="replace")


def _classify(line: str) -> str | None:
    key = line.strip().lower().rstrip(":")
    if len(key) > 40:
        return None
    for section, aliases in _SECTION_HEADS.items():
        if key in aliases:
            return section
    return None


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

    buckets: dict[str, list[str]] = {k: [] for k in _SECTION_HEADS}
    current: str | None = None
    for line in lines:
        section = _classify(line)
        if section:
            current = section
            continue
        if current and line.strip():
            buckets[current].append(line)

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
            if entry is not None and entry["bullets"]:
                entries.append(entry)
                entry = None
            header = line.strip()
            if not header:
                continue
            dates = ""
            match = re.search(r"([A-Za-z]{3,9}\.?\s?\d{4}.*)$", header)
            if match and _DATE_RE.search(match.group(1)):
                dates = match.group(1).strip()
                header = header[: match.start()].strip(" -–—|")
            parts = re.split(r"\s+[-–—|]\s+", header, maxsplit=1)
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


class ParsingService:
    def __init__(
        self,
        resumes: ResumeRepository,
        vectors: VectorRepository | None = None,
    ) -> None:
        self.resumes = resumes
        # Optional so existing construction sites keep working.
        self.vectors = vectors

    def parse_resume(self, resume_id, user_id) -> Resume:
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        try:
            content = storage.get_object(settings.s3_bucket_uploads, resume.storage_key)
            filename = resume.storage_key.rsplit("/", 1)[-1]
            raw = extract_text(filename, content)
            resume.raw_text = raw
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
