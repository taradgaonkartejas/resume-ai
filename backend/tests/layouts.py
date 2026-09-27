"""A corpus of real-world resume layouts, with the parse we expect from each.

Why this exists: `structure_text` is a regex segmenter, and every fix to it
trades one layout against another. Without a corpus, fixing the wrapped-bullet
case silently breaks the standalone-date case and nobody notices until a user
uploads a CV and gets a fabricated employer called "deploy time by 60%".

Each entry asserts only what actually matters -- company, role, whether dates
were captured, and the bullets -- not the full document, so adding a field to
empty_resume() does not invalidate the corpus.

Add a layout here whenever a real file parses badly. That is the point.
"""

LAYOUTS: list[dict] = [
    # ------------------------------------------------------------------ 1
    {
        "name": "dates_inline_with_header",
        "why": "The simplest layout: everything on the header line.",
        "text": """Jane Doe
jane@example.com | +1 555 0100

EXPERIENCE
Acme Corp - Senior Engineer - Jan 2020 - Present
- Led the platform migration to Kubernetes, cutting deploy time by 60%.
- Built Terraform modules adopted by 8 teams.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the platform migration to Kubernetes, cutting deploy time by 60%.",
                    "Built Terraform modules adopted by 8 teams.",
                ],
            }
        ],
    },
    # ------------------------------------------------------------------ 2
    {
        "name": "standalone_date_line",
        "why": "§1.2b — dates on their own line used to WIPE company and role.",
        "text": """Jane Doe
jane@example.com

EXPERIENCE
Acme Corp - Senior Engineer
Jan 2020 - Present
- Led the platform migration to Kubernetes.
- Built Terraform modules adopted by 8 teams.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the platform migration to Kubernetes.",
                    "Built Terraform modules adopted by 8 teams.",
                ],
            }
        ],
    },
    # ------------------------------------------------------------------ 3
    {
        "name": "wrapped_bullet_continuation",
        "why": "§1.2a — a wrapped line became a fabricated company.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer - 2020-2024
- Led the platform migration to Kubernetes across 40 services, cutting
  deploy time by 60% and improving reliability for every downstream team.
- Built Terraform modules.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the platform migration to Kubernetes across 40 services, cutting "
                    "deploy time by 60% and improving reliability for every downstream team.",
                    "Built Terraform modules.",
                ],
            }
        ],
    },
    # ------------------------------------------------------------------ 4
    {
        "name": "numbered_bullets",
        "why": "§3.6 regression guard — used to yield zero entries.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer - 2020-2024
1. Led the platform migration to Kubernetes.
2) Built Terraform modules adopted by 8 teams.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the platform migration to Kubernetes.",
                    "Built Terraform modules adopted by 8 teams.",
                ],
            }
        ],
    },
    # ------------------------------------------------------------------ 5
    {
        "name": "mixed_bullet_glyphs",
        "why": "Regression guard for the original glyph set.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Engineer - 2020-2024
- Dash bullet.
\u2022 Unicode bullet.
* Asterisk bullet.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Engineer",
                "has_dates": True,
                "bullets": ["Dash bullet.", "Unicode bullet.", "Asterisk bullet."],
            }
        ],
    },
    # ------------------------------------------------------------------ 6
    {
        "name": "two_employers",
        "why": "A real header must still close the previous entry.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer - 2022-2024
- Led the migration.
Globex Inc - Engineer - 2019-2022
- Built the billing service.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": ["Led the migration."],
            },
            {
                "company": "Globex Inc",
                "role": "Engineer",
                "has_dates": True,
                "bullets": ["Built the billing service."],
            },
        ],
    },
    # ------------------------------------------------------------------ 7
    {
        "name": "standalone_dates_two_employers",
        "why": "The §1.2b fix must not merge two jobs into one.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer
Jan 2022 - Present
- Led the migration.
Globex Inc - Engineer
Mar 2019 - Dec 2021
- Built the billing service.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": ["Led the migration."],
            },
            {
                "company": "Globex Inc",
                "role": "Engineer",
                "has_dates": True,
                "bullets": ["Built the billing service."],
            },
        ],
    },
    # ------------------------------------------------------------------ 8
    {
        "name": "wrapped_then_new_employer",
        "why": "A continuation must not swallow the next real header.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer - 2022-2024
- Led the migration of 40 services to Kubernetes, cutting deploy
  time by 60% across the platform.
Globex Inc - Engineer - 2019-2022
- Built the billing service.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the migration of 40 services to Kubernetes, cutting deploy "
                    "time by 60% across the platform."
                ],
            },
            {
                "company": "Globex Inc",
                "role": "Engineer",
                "has_dates": True,
                "bullets": ["Built the billing service."],
            },
        ],
    },
    # ------------------------------------------------------------------ 9
    {
        "name": "pipe_separated_header",
        "why": "Pipes are as common as dashes as a header separator.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp | Senior Engineer | 2020-2024
- Led the migration.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": ["Led the migration."],
            }
        ],
    },
    # ----------------------------------------------------------------- 10
    {
        "name": "em_dash_header",
        "why": "Word auto-replaces ' - ' with ' \u2014 '.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp \u2014 Senior Engineer \u2014 Jan 2020 \u2013 Present
- Led the migration.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": ["Led the migration."],
            }
        ],
    },
    # ----------------------------------------------------------------- 11
    {
        "name": "skills_and_education",
        "why": "Other sections must survive the experience changes.",
        "text": """Jane Doe
jane@example.com

SUMMARY
Platform engineer with eight years of experience running production systems.

EXPERIENCE
Acme Corp - Senior Engineer - 2020-2024
- Led the migration.

SKILLS
Platform: Kubernetes, Docker, Terraform
Languages: Python, Go

EDUCATION
State University - BSc Computer Science - 2016
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": ["Led the migration."],
            }
        ],
        "min_skills": 2,
        "min_education": 1,
        "summary_contains": "Platform engineer",
    },
    # ----------------------------------------------------------------- 12
    {
        "name": "wrapped_with_standalone_dates",
        "why": "Both bugs in one document, which is how they actually arrive.",
        "text": """Jane Doe

EXPERIENCE
Acme Corp - Senior Engineer
Jan 2020 - Present
- Led the platform migration to Kubernetes across 40 services, cutting
  deploy time by 60% for every downstream team.
- Built Terraform modules.
""",
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "has_dates": True,
                "bullets": [
                    "Led the platform migration to Kubernetes across 40 services, cutting "
                    "deploy time by 60% for every downstream team.",
                    "Built Terraform modules.",
                ],
            }
        ],
    },
]
