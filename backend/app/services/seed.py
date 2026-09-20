"""Idempotent demo data: 5 users, 5 templates, a sample JD and one resume."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobDescription, Resume, ResumeVersion, Template, User

USERS = [
    ("Priya Sharma", "priya@example.com", "Site Reliability Engineer", "#2563eb"),
    ("Arjun Mehta", "arjun@example.com", "Backend Engineer", "#16a34a"),
    ("Sara Iyer", "sara@example.com", "Product Manager", "#db2777"),
    ("Daniel Okafor", "daniel@example.com", "Data Engineer", "#ea580c"),
    ("Lena Fischer", "lena@example.com", "Frontend Engineer", "#7c3aed"),
]

TEMPLATES = [
    ("modern", "Modern", "Clean sans-serif with accent rules."),
    ("classic", "Classic", "Traditional serif, conservative spacing."),
    ("compact", "Compact", "Dense layout that fits more on one page."),
    ("executive", "Executive", "Generous whitespace, senior-leaning."),
    ("minimal", "Minimal", "No ornament, maximum readability."),
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
"""

DEMO_RESUME = {
    "contact": {
        "name": "Priya Sharma",
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
            "dates": "2021 - Present",
            "bullets": [
                "Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
                "Built Terraform modules adopted by 8 engineering teams",
                "Reduced pager volume by 45% by rewriting alert rules around SLOs",
            ],
        },
        {
            "company": "Globex",
            "role": "Infrastructure Engineer",
            "dates": "2019 - 2021",
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
        {"school": "COEP Pune", "degree": "B.Tech Computer Engineering", "dates": "2015 - 2019"}
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
        "vector_docs": 0,
    }

    for key, name, description in TEMPLATES:
        if db.scalar(select(Template).where(Template.key == key)) is None:
            db.add(Template(key=key, name=name, description=description, design_tokens={}))
            created["templates"] += 1
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
            structured_data=DEMO_RESUME,
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
    created["vector_docs"] = _seed_vectors(db)

    db.commit()
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
