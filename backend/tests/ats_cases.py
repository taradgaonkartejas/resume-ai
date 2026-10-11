"""Pinned expectations for the ATS agent's acronym expansions.

Expansion is a question of FACT, not judgement, which is why it
survived the cull that removed section relabelling from this file.
"PMP = Project Management Process" passes every structural guard we
have and is wrong, so the only defence is pinned expected values.
"""

from __future__ import annotations

# A minimal resume the skills get dropped into. Deliberately thin: the agent
# is being asked about the acronyms, and surrounding content would give it
# extra cues that the real failure case does not have.
BASE: dict = {
    "contact": {"name": "Jane Doe", "email": "jane@example.com", "phone": "+91 80000 00000"},
    "summary": {"text": "Engineer with eight years building payment systems."},
    "experience": [],
    "education": [],
    "skills": [{"label": "", "items": ["Python", "PostgreSQL"]}],
    "extras": [],
}


# ====================================================== DELETED: relabelling
# This file used to carry 18 section-relabel cases (8 positive, 10 negative)
# with a DEV/HOLDOUT split. They were deleted with the feature they measured.
#
# The corpus did its job. Run on 2026-10-11 against the DEV split it reported
# 100% recall and 100% target accuracy on positives, and a 17% false-positive
# rate against a kill criterion of 10% that had been registered in advance.
# The one false positive was "Leadership & Achievements" from a real resume,
# on 3 runs out of 3 -- the same case the rule-based version had already
# failed on. Two independent implementations, same error, so the error is in
# the premise: the detectable shape of "work history under a creative
# heading" and of "a genuine achievements section" is identical.
#
# Two caveats recorded because they cut against the feature, not for it:
# the 10% threshold was unexpressible on 6 negatives (the granularity is
# 17%), so it was really a zero-false-positive gate; and a rate-limited call
# degrades to silence, which scores as a correct negative, making 17% a
# floor rather than an estimate.
#
# Keeping the cases would have left a corpus that tests nothing. The numbers
# are preserved here and in `agents._rule_ats_review`.

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
