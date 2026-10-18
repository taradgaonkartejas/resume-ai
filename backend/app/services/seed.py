"""Idempotent demo data: 5 users, 5 templates, a sample JD and one resume."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    JobDescription,
    Resume,
    ResumeVersion,
    Suggestion,
    TailoringSession,
    Template,
    User,
)
from app.services.resume_ops import migrate
from app.services.resume_ops import resolve as resume_ops_resolve

USERS = [
    ("Priya Sharma", "priya@example.com", "Site Reliability Engineer", "#2563eb"),
    ("Arjun Mehta", "arjun@example.com", "Backend Engineer", "#16a34a"),
    ("Sara Iyer", "sara@example.com", "Product Manager", "#db2777"),
    ("Daniel Okafor", "daniel@example.com", "Data Engineer", "#ea580c"),
    ("Lena Fischer", "lena@example.com", "Frontend Engineer", "#7c3aed"),
]

# Templates: (key, name, description, design_tokens)
#
# design_tokens is the CONTRACT the renderers read. It used to be {} for every
# template, which meant "pick a template" could not change anything -- the
# frontend had to hardcode its own per-key styling and the PDF ignored the
# choice entirely. Putting the values here makes one definition drive the live
# preview, the PDF and any future renderer.
#
# Token meanings:
#   font        "sans" | "serif"                 body typeface family
#   heading     "rule" | "underline" | "plain" | "boxed" | "sidebar"
#               how a section heading is separated from its content
#   name_align  "left" | "center"                header block alignment
#   accent      hex, or "" to inherit the neutral ink colour
#   density     "airy" | "normal" | "dense"      vertical rhythm multiplier
#   caps        bool                             UPPERCASE section headings
#   divider     bool                             rule under the contact line
TEMPLATES = [
    (
        "modern",
        "Modern",
        "Accent rules and a clean left-aligned header.",
        {
            "font": "sans",
            "heading": "rule",
            "name_align": "left",
            "accent": "#4737ff",
            "density": "normal",
            "caps": True,
            "divider": False,
            "header": "stacked",
            "entry": "stacked",
            "skill_columns": 2,
        },
    ),
    (
        "executive",
        "Executive",
        "Serif with a split header and warm copper accents.",
        {
            "font": "serif",
            "heading": "rule",
            "name_align": "left",
            "accent": "#A2643C",
            "density": "normal",
            "caps": True,
            "divider": True,
            "header": "split",
            "entry": "stacked",
            "skill_columns": 3,
        },
    ),
    (
        "balanced",
        "Balanced",
        "Centred header with muted green section titles.",
        {
            "font": "sans",
            "heading": "plain",
            "name_align": "center",
            "accent": "#5F8A7D",
            "density": "normal",
            "caps": False,
            "divider": False,
            "header": "stacked",
            "entry": "stacked",
            "skill_columns": 1,
        },
    ),
    (
        "classic",
        "Classic",
        "Traditional serif, centred and understated.",
        {
            "font": "serif",
            "heading": "underline",
            "name_align": "center",
            "accent": "",
            "density": "normal",
            "caps": True,
            "divider": True,
            "header": "stacked",
            "entry": "stacked",
            "skill_columns": 1,
        },
    ),
    (
        "minimal",
        "Minimal",
        "Airy monochrome layout with generous whitespace.",
        {
            "font": "sans",
            "heading": "rule",
            "name_align": "center",
            "accent": "",
            "density": "airy",
            "caps": True,
            "divider": False,
            "header": "stacked",
            "entry": "stacked",
            "skill_columns": 2,
        },
    ),
    (
        "compact",
        "Compact",
        "Dense single-page layout with three-column skills.",
        {
            "font": "sans",
            "heading": "plain",
            "name_align": "left",
            "accent": "",
            "density": "dense",
            "caps": True,
            "divider": True,
            "header": "split",
            "entry": "inline",
            "skill_columns": 3,
        },
    ),
    (
        "technical",
        "Technical",
        "Bar-led headings and tight spacing for engineering roles.",
        {
            "font": "sans",
            "heading": "sidebar",
            "name_align": "left",
            "accent": "#3529bf",
            "density": "dense",
            "caps": False,
            "divider": False,
            "header": "stacked",
            "entry": "inline",
            "skill_columns": 3,
        },
    ),
]

SAMPLE_JD = """Senior Site Reliability Engineer

We are looking for an SRE to own reliability across our Kubernetes platform.

Responsibilities:
- Operate and scale Kubernetes clusters across multiple AWS regions
- Build Terraform modules and CI/CD pipelines for self-service deployment
- Define SLOs, drive incident response, and reduce mean time to recovery
- Improve observability with Prometheus and Grafana

Requirements:
- Strong Python or Go
- Production Kubernetes and Docker experience
- Terraform, AWS, PostgreSQL
- Experience with GitOps and progressive delivery
- 3+ years with Redis or a comparable caching layer
- Comfortable working in an Agile team
"""

DEMO_RESUME = {
    "contact": {
        "name": "Priya Sharma",
        "headline": "Site Reliability Engineer",
        "email": "priya@example.com",
        "phone": "+91 98765 43210",
        "location": "Pune, India",
        "links": ["github.com/priyasharma"],
    },
    "summary": {
        "text": (
            "Site reliability engineer with six years running production "
            "infrastructure for high-traffic services. Focused on reducing "
            "operational toil through automation."
        )
    },
    "experience": [
        {
            "company": "Acme Corp",
            "role": "Senior SRE",
            "location": "Pune, India",
            "start_date": "Mar 2021",
            "end_date": "",
            "current": True,
            "bullets": [
                "Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
                "Built Terraform modules adopted by 8 engineering teams",
                "Reduced pager volume by 45% by rewriting alert rules around SLOs",
            ],
        },
        {
            "company": "Globex",
            "role": "Infrastructure Engineer",
            "location": "Bengaluru, India",
            "start_date": "Jul 2019",
            "end_date": "Feb 2021",
            "current": False,
            "bullets": [
                "Automated PostgreSQL failover, improving recovery time to under 2 minutes",
                "Introduced CI pipelines that cut build times from 22 to 7 minutes",
            ],
        },
    ],
    "projects": [
        {
            "name": "kubewatch",
            "bullets": ["Open-source controller that reports drift in cluster manifests"],
        }
    ],
    "education": [
        {
            "school": "COEP Pune",
            "degree": "B.Tech Computer Engineering",
            "field_of_study": "Computer Engineering",
            "location": "Pune, India",
            "grade": "First Class with Distinction",
            "start_date": "2015",
            "end_date": "2019",
            "current": False,
        }
    ],
    "skills": [
        {"label": "Platform", "items": ["Kubernetes", "Docker", "Terraform", "AWS"]},
        {"label": "Languages", "items": ["Python", "Go", "Bash"]},
        {"label": "Observability", "items": ["Prometheus", "Grafana"]},
    ],
}


def seed(db: Session) -> dict:
    created = {
        "users": 0,
        "templates": 0,
        "resumes": 0,
        "job_descriptions": 0,
        "suggestions": 0,
        "tailoring_sessions": 0,
        "vector_docs": 0,
    }

    # Templates are PRESENTATION, not user data: nothing the user typed lives
    # here, so unlike users and resumes these rows are safe to update in place.
    # Insert-only would strand every existing database on the old empty
    # design_tokens and a "new template" would never appear after first boot.
    for key, name, description, tokens in TEMPLATES:
        existing = db.scalar(select(Template).where(Template.key == key))
        if existing is None:
            db.add(
                Template(key=key, name=name, description=description, design_tokens=tokens)
            )
            created["templates"] += 1
        else:
            # Refresh in place. `created` still counts inserts only, so the
            # idempotency test ("second seed creates nothing") holds.
            existing.name = name
            existing.description = description
            existing.design_tokens = tokens
    db.flush()

    users: list[User] = []
    for name, email, title, colour in USERS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(name=name, email=email, title=title, avatar_color=colour)
            db.add(user)
            created["users"] += 1
        users.append(user)
    db.flush()

    if db.scalar(select(JobDescription).where(JobDescription.is_sample.is_(True))) is None:
        from app.services.heuristics import extract_keywords

        db.add(
            JobDescription(
                title="Senior Site Reliability Engineer",
                content=SAMPLE_JD,
                extracted_keywords=extract_keywords(SAMPLE_JD),
                is_sample=True,
            )
        )
        created["job_descriptions"] += 1

    priya = users[0]
    existing = db.scalar(select(Resume).where(Resume.user_id == priya.id))
    if existing is None:
        resume = Resume(
            user_id=priya.id,
            title="Priya Sharma — SRE",
            # migrate() fills the derived `dates` mirror, so seeded data is
            # byte-identical in shape to anything the editor writes.
            structured_data=migrate(DEMO_RESUME),
            parse_status="ready",
            template_key="modern",
            version_cursor=1,
        )
        db.add(resume)
        db.flush()
        db.add(
            ResumeVersion(
                resume_id=resume.id,
                seq=1,
                snapshot=DEMO_RESUME,
                change_source="seed",
                label="Seeded",
            )
        )
        created["resumes"] += 1

    db.flush()
    created.update(_seed_tailoring(db))
    created["vector_docs"] = _seed_vectors(db)

    db.commit()
    return created


def _seed_tailoring(db: Session) -> dict:
    """A finished tailoring session, including two unanswered questions.

    Without this the `needs_input` state is unreachable without live API
    keys: the only other way to produce one is a real tailoring run. A
    feature that cannot be seen without a paid key does not get reviewed,
    and this is the riskiest feature in the product.

    The cards are not hand-written. The gap keywords and the question text
    come from the same `match_keywords` / `questions_for_gaps` the live path
    uses, so the seed cannot drift away from what the engine actually does
    -- if the filter changes, this data changes with it.
    """
    from app.services.clarifying import questions_for_gaps
    from app.services.heuristics import match_keywords

    created = {"tailoring_sessions": 0, "suggestions": 0}

    resume = db.scalar(select(Resume).order_by(Resume.created_at))
    jd = db.scalar(select(JobDescription).where(JobDescription.is_sample.is_(True)))
    if resume is None or jd is None:
        return created
    # Insert-only, the same guard the resume uses, so a second seed creates
    # nothing and the idempotency test stays honest.
    if db.scalar(select(TailoringSession).where(TailoringSession.resume_id == resume.id)):
        return created

    data = resume.structured_data or {}
    scores = match_keywords(data, jd.extracted_keywords or [])
    session = TailoringSession(
        resume_id=resume.id,
        job_description_id=jd.id,
        thread_id="tailor-seed",
        match_percent=scores["match_percent"],
        baseline_percent=scores["match_percent"],
        matched_keywords=scores["matched_keywords"],
        gap_keywords=scores["gap_keywords"],
        all_keywords=scores["all_keywords"],
        graph_state="awaiting_review",
    )
    db.add(session)
    db.flush()
    created["tailoring_sessions"] = 1

    questions = questions_for_gaps(scores["gap_keywords"], jd.content)
    # The last one is seeded as ALREADY ANSWERED, so the post-attestation
    # state is visible too. It is removed from the open set rather than
    # duplicated: a card cannot be both an unanswered question and a
    # confirmed fact about the same skill.
    answered = questions.pop() if len(questions) > 2 else None
    for q in questions:
        db.add(Suggestion(
            resume_id=resume.id,
            session_id=session.id,
            origin="tailor",
            section="skills",
            target_ref="",
            placement=f"Job description asks for {q['label']}",
            keywords=[q["keyword"]],
            status="needs_input",
            grounded=False,
            question=q["question"],
            requires_text=q["requires_text"],
        ))
        created["suggestions"] += 1

    # One ordinary pending card beside the questions. The UI risk in this
    # increment is not either card alone, it is the two shapes in one list.
    if data.get("experience") and (data["experience"][0].get("bullets") or []):
        original = data["experience"][0]["bullets"][0]
        db.add(Suggestion(
            resume_id=resume.id,
            session_id=session.id,
            origin="tailor",
            section="experience",
            # `exp_0.bullet_0`, not `experience.0.bullets.0`: the long form
            # is not a ref resume_ops understands, so this demo card raised
            # InvalidTargetRef the moment anyone clicked Accept.
            target_ref="exp_0.bullet_0",
            placement="Acme Corp - Senior SRE",
            original_text=original,
            # Must actually differ from the original. A seeded card whose
            # draft equals its source accepts to a no-op and teaches the
            # reader nothing about what accepting does.
            suggested_text=(
                "Led migration of 40 services to Kubernetes, cutting deploy "
                "time by 65% and standardising rollouts across the platform"
            ),
            keywords=["kubernetes"],
            reasoning="Mirrors the job description's phrasing for this duty.",
            status="pending",
            grounded=True,
        ))
        created["suggestions"] += 1

    # And one already-answered card, so the post-attestation state is
    # visible without anyone having to answer a question first.
    if answered is not None:
        skills_original = (
            resume_ops_resolve(data, "skills.0.items") if data.get("skills") else ""
        )
        db.add(Suggestion(
            resume_id=resume.id,
            session_id=session.id,
            origin="tailor",
            section="skills",
            target_ref="skills.0.items",
            placement=f"Job description asks for {answered['label']}",
            original_text=skills_original,
            suggested_text=", ".join(
                [*[i.strip() for i in skills_original.split(",") if i.strip()],
                 answered["label"]]
            ),
            keywords=[answered["keyword"]],
            reasoning=f"You confirmed {answered['label']} when asked.",
            status="pending",
            grounded=False,
            attested=True,
            question=answered["question"],
            answer=f"Used {answered['label']} for two years on the platform team.",
            requires_text=answered["requires_text"],
        ))
        created["suggestions"] += 1

    db.flush()
    return created


def _seed_vectors(db: Session) -> int:
    """Index the global RAG corpora and every seeded resume's bullets.

    Without this the vector tables are empty and retrieval silently returns
    nothing, so the agents would run ungrounded.
    """
    try:
        from app.ai.corpora import ATS_RULES, SKILL_TAXONOMY
        from app.ai.vectorstore import VectorStore
        from app.repositories.vector_repository import VectorRepository
    except Exception as exc:  # noqa: BLE001
        print(f"  (vector seeding skipped: {exc})")
        return 0

    store = VectorStore(VectorRepository(db))
    total = store.index_global("skill_taxonomy", SKILL_TAXONOMY)
    total += store.index_global("ats_rules", ATS_RULES)

    for resume in db.scalars(select(Resume)):
        total += store.index_resume(
            user_id=resume.user_id,
            resume_id=resume.id,
            structured_data=resume.structured_data or {},
        )
    return total


def main() -> None:
    from app.db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        print(seed(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
