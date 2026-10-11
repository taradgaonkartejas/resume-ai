"""Labelled sections for measuring the ATS agent's judgement.

Same problem as the critic corpus and the same answer: you cannot assertEqual
a judgement, so you hand it cases where the right answer is already known and
count the agreements.

**The definition, written down before any model was run.**

    A section SHOULD be relabelled iff its entries are formal roles at an
    organisation with dates -- employment, education, or volunteering -- AND
    its heading contains none of the vocabulary an ATS matches on.

Note it admits three different correct targets, so the corpus measures *which*
label the agent picks, not merely whether it fires.

**Why the negatives outnumber the positives.** The costs are not symmetric.
Missing a hidden work history costs the user a little. Telling a genuine
"Leadership & Achievements" section to rename itself costs them a hand-edit on
a document that was already fine, and teaches them to ignore every other
suggestion the product makes. The rule-based version of this feature did
exactly that on a real resume, which is why it was deleted -- see
`agents._rule_ats_review`. So the hard negatives are the real exam, and
`leadership_achievements` below is that exact failure, kept as case law.

**Where the headings come from.** The non-standard headings are taken from
real resumes and public resume templates, not invented. An invented set is
biased: whoever writes the cases and whoever writes the prompt share a prior
about what a "weird heading" looks like, so the model scores well on its
author's imagination. Harvested headings break that.

**Hold out the last third.** `DEV` is for prompt iteration, `HOLDOUT` is
measured once at the end. Tuning against all of them and then quoting the
total measures nothing but how hard you tuned.

Each case:
    id            stable name
    why           the reasoning, in one line
    section       the extras section as it appears in structured_data
    expect        "relabel" | "leave"
    expect_target the standard heading, when expect == "relabel"
"""

from __future__ import annotations

# A minimal resume the section gets dropped into. Deliberately thin: the
# agent is being asked about the section, and surrounding content would give
# it extra cues that the real failure case does not have.
BASE: dict = {
    "contact": {"name": "Jane Doe", "email": "jane@example.com", "phone": "+91 80000 00000"},
    "summary": {"text": "Engineer with eight years building payment systems."},
    "experience": [],
    "education": [],
    "skills": [{"label": "", "items": ["Python", "PostgreSQL"]}],
    "extras": [],
}


def _entry(primary: str, secondary: str = "", date: str = "", detail: str = "") -> dict:
    return {"primary": primary, "secondary": secondary, "date": date, "detail": detail}


def _custom(title: str, entries: list[dict]) -> dict:
    return {"kind": "custom", "title": title, "entries": entries}


# ============================================================ true positives
# Real work history behind a heading an ATS does not match on.
_POSITIVES: list[dict] = [
    {
        "id": "my_journey",
        "why": "dated roles at named employers; the canonical failure",
        "section": _custom("My Journey", [
            _entry("Senior Engineer", "Globex", "2018 - 2020"),
            _entry("Engineer", "Initech", "2016 - 2018"),
        ]),
        "expect": "relabel",
        "expect_target": "Work Experience",
    },
    {
        "id": "where_ive_been",
        "why": "same content, chattier heading",
        "section": _custom("Where I've Been", [
            _entry("Platform Lead", "Hooli", "Mar 2021 - Present"),
            _entry("Backend Engineer", "Pied Piper", "2019 - 2021"),
        ]),
        "expect": "relabel",
        "expect_target": "Work Experience",
    },
    {
        "id": "professional_chapters",
        "why": "ornamental heading over ordinary employment",
        "section": _custom("Professional Chapters", [
            _entry("Data Engineer", "Umbrella Analytics", "2020 - 2023"),
            _entry("Analyst", "Umbrella Analytics", "2018 - 2020"),
        ]),
        "expect": "relabel",
        "expect_target": "Work Experience",
    },
    {
        "id": "career_so_far",
        "why": "common on UK/India CVs",
        "section": _custom("Career So Far", [
            _entry("Site Reliability Engineer", "Nimbus Cloud", "Jun 2022 - Present"),
            _entry("Systems Engineer", "Nimbus Cloud", "Jan 2020 - Jun 2022"),
        ]),
        "expect": "relabel",
        "expect_target": "Work Experience",
    },
    {
        "id": "the_work",
        "why": "terse creative heading, designer-style resume",
        "section": _custom("The Work", [
            _entry("Product Designer", "Studio Verde", "2021 - 2024"),
            _entry("Junior Designer", "Studio Verde", "2019 - 2021"),
        ]),
        "expect": "relabel",
        "expect_target": "Work Experience",
    },
    {
        "id": "academic_background",
        "why": "education, not employment -- target must differ",
        "section": _custom("Academic Background", [
            _entry("B.E. Computer Engineering", "Pune University", "2012 - 2016"),
            _entry("Higher Secondary", "Fergusson College", "2010 - 2012"),
        ]),
        "expect": "relabel",
        "expect_target": "Education",
    },
    {
        "id": "studies_and_training",
        "why": "education behind a compound heading",
        "section": _custom("Studies & Training", [
            _entry("M.Sc. Data Science", "IIT Bombay", "2017 - 2019"),
        ]),
        "expect": "relabel",
        "expect_target": "Education",
    },
    {
        "id": "giving_back",
        "why": "dated formal roles at organisations, but unpaid",
        "section": _custom("Giving Back", [
            _entry("Mentor", "Code for India", "2021 - Present"),
            _entry("Workshop Lead", "Pune Tech Collective", "2019 - 2021"),
        ]),
        "expect": "relabel",
        "expect_target": "Volunteer Experience",
    },
]


# ============================================================ hard negatives
# Structurally identical to the positives -- dated, organisation-shaped,
# job-like -- and all of them correctly named already. This is the exam.
_NEGATIVES: list[dict] = [
    {
        "id": "leadership_achievements",
        "why": "CASE LAW: the rule version flagged this on a real resume and was wrong",
        "section": _custom("Leadership & Achievements", [
            _entry("Founder", "Kalarpan", "2019 - Present",
                   "Charity funding donations through staged theatrical performances."),
            _entry("Captain", "College Football Team", "2014 - 2015"),
        ]),
        "expect": "leave",
    },
    {
        "id": "publications",
        "why": "dated, has venues that look like employers; genuinely publications",
        "section": _custom("Publications", [
            _entry("Latency-aware sharding for OLTP workloads", "VLDB", "2023"),
            _entry("A note on idempotent retries", "ACM Queue", "2021"),
        ]),
        "expect": "leave",
    },
    {
        "id": "speaking",
        "why": "conference names read like employers",
        "section": _custom("Speaking", [
            _entry("Scaling Postgres past a terabyte", "PyCon India", "2023"),
            _entry("Why your retries make it worse", "RootConf", "2022"),
        ]),
        "expect": "leave",
    },
    {
        "id": "awards",
        "why": "dated, awarded by organisations, not roles",
        "section": _custom("Awards", [
            _entry("Engineering Excellence Award", "Globex", "2022"),
            _entry("Best Paper", "VLDB", "2023"),
        ]),
        "expect": "leave",
    },
    {
        "id": "patents",
        "why": "dated and assigned to a company; not employment",
        "section": _custom("Patents", [
            _entry("Method for adaptive rate limiting", "US 11,234,567", "2022"),
        ]),
        "expect": "leave",
    },
    {
        "id": "open_source",
        "why": "sustained dated involvement with named projects -- the closest call here",
        "section": _custom("Open Source", [
            _entry("Maintainer", "pgvector-python", "2021 - Present"),
            _entry("Contributor", "FastAPI", "2020 - 2021"),
        ]),
        "expect": "leave",
    },
    {
        "id": "professional_memberships",
        "why": "dated affiliations with organisations; not roles",
        "section": _custom("Professional Memberships", [
            _entry("Member", "IEEE Computer Society", "2018 - Present"),
            _entry("Member", "ACM", "2017 - Present"),
        ]),
        "expect": "leave",
    },
    {
        "id": "side_projects",
        "why": "Projects is a standard heading, but this one is ALREADY standard enough",
        "section": _custom("Side Projects", [
            _entry("Ledger CLI", "personal", "2022"),
            _entry("Train delay tracker", "personal", "2021"),
        ]),
        "expect": "leave",
    },
    {
        "id": "languages_spoken",
        "why": "dated-looking proficiency levels; nothing to rename",
        "section": _custom("Languages", [
            _entry("English", "Native"),
            _entry("Marathi", "Native"),
            _entry("German", "B1"),
        ]),
        "expect": "leave",
    },
    {
        "id": "interests",
        "why": "no dates, no organisations -- must not fire",
        "section": _custom("Interests", [
            _entry("Long-distance running"),
            _entry("Classical percussion"),
        ]),
        "expect": "leave",
    },
]


CASES: list[dict] = _POSITIVES + _NEGATIVES

# Two-thirds for prompt iteration, one-third measured once.
#
# Assigned by id rather than sliced. A slice looked fine and was not: it put
# both gate-filtered negatives (`languages_spoken`, `interests`) in the
# holdout, leaving only two negatives that actually reach a model. A holdout
# whose negatives are mostly handled by deterministic code before the agent
# sees them measures the gate, not the judgement, and would have reported a
# reassuring false-positive rate off a two-case sample.
#
# So: the free ones live in DEV, and the HOLDOUT carries four hard negatives
# that all reach the model. `leadership_achievements` stays in DEV on purpose
# -- it is the known failure, so prompt work should be aimed at it, and a
# case you have been iterating against cannot also be your measurement.
_DEV_IDS = {
    "my_journey", "where_ive_been", "professional_chapters",
    "academic_background", "giving_back",
    "leadership_achievements", "publications", "speaking", "patents",
    "languages_spoken", "interests",
}

DEV: list[dict] = [c for c in CASES if c["id"] in _DEV_IDS]
HOLDOUT: list[dict] = [c for c in CASES if c["id"] not in _DEV_IDS]


def resume_for(case: dict) -> dict:
    """BASE with this case's section attached."""
    return {**BASE, "extras": [case["section"]]}


def positives(cases: list[dict] | None = None) -> list[dict]:
    return [c for c in (cases or CASES) if c["expect"] == "relabel"]


def negatives(cases: list[dict] | None = None) -> list[dict]:
    return [c for c in (cases or CASES) if c["expect"] == "leave"]


# ====================================================== acronym expansions
# NOT judgement -- fact. The existing guard checks the acronym is really on
# the resume and that the expansion is longer than it; it cannot check that
# the expansion is CORRECT. "PMP = Project Management Process" passes every
# programmatic test we have and is wrong, so the only defence is pinned
# expected values.
#
# `accept` holds the forms we will count as correct, lowercased and compared
# on substring, because wording varies legitimately ("Certified Kubernetes
# Administrator" vs "Kubernetes Administrator, Certified").
EXPANSIONS: list[dict] = [
    {"acronym": "PMP", "accept": ["project management professional"]},
    {"acronym": "CKA", "accept": ["certified kubernetes administrator"]},
    {"acronym": "CISSP", "accept": ["certified information systems security professional"]},
    {"acronym": "CPA", "accept": ["certified public accountant"]},
    {"acronym": "AWS", "accept": ["amazon web services"]},
    {"acronym": "GCP", "accept": ["google cloud platform"]},
    {"acronym": "CFA", "accept": ["chartered financial analyst"]},
    {"acronym": "RHCE", "accept": ["red hat certified engineer"]},
    # Decoys: these are already the normal name of the thing. Expanding them
    # is wrong, not merely unnecessary, so the right answer is to say nothing.
    {"acronym": "SQL", "accept": [], "decoy": True},
    {"acronym": "REST", "accept": [], "decoy": True},
]


def expansion_resume(acronyms: list[str]) -> dict:
    """BASE with the given acronyms as skills."""
    return {**BASE, "skills": [{"label": "", "items": ["Python", *acronyms]}]}
