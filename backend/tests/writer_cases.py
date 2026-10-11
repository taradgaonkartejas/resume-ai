"""Labelled rewrites for measuring the WRITER.

The critic has had a corpus since day one; the writer -- the half users
actually read -- has never been measured at all. Every claim about rewrite
quality in this project has so far been an impression.

The trap with a generative benchmark is that there is no single correct output
string, so the obvious design (store a "known-good" rewrite and diff against
it) measures similarity to one arbitrary answer rather than quality. This file
avoids that entirely: **the scorer already knows what "fixed" means.**

Every case starts from BASE, which scores 100/100 with zero findings, and
applies one patch that introduces exactly one finding. The writer is then
asked to fix that finding, and the result is re-scored. Four mechanical
questions, no human judgement and no reference string:

  fixed        re-running the heuristics no longer produces that finding
  faithful     no digit appears that was not already in the resume, and the
               production critic approves the draft
  no_regression  no NEW finding id appeared -- a rewrite that fixes
               `weak_verbs` by writing sixty words has traded one problem
               for another
  changed      it is not a no-op

`expect` distinguishes two honest outcomes:

  fix      the finding can be repaired from facts already in the document
  decline  repairing it REQUIRES a fact the resume does not contain -- a
           metric nobody has stated. The correct behaviour is to refuse, not
           to invent. `summary.no_metric` and `experience.unquantified_bullets`
           are in this group, and a writer that "fixes" them has fabricated.

That second group matters more than it looks. It is the exact case that
produced the 422 bug: the writer could not fix `summary.no_metric` without
inventing a number, and the service reported that as a verdict about the
user's resume. Here it is a labelled expectation instead of a surprise.

**Faithfulness is the safety metric and outranks fix rate.** A writer that
fixes everything by inventing numbers scores 100% on fixes and is worse than
useless -- the same shape as a critic that rejects everything scoring 100% on
recall.
"""

from app.services import resume_ops

BASE = resume_ops.migrate({
    "contact": {"name": "Priya Sharma", "headline": "Senior Platform Engineer",
                "email": "priya@example.com", "phone": "+91 98765 43210",
                "location": "Pune, India", "links": ["linkedin.com/in/priyasharma"]},
    "summary": {"text": "Senior platform engineer with 6 years building and operating "
                        "cloud infrastructure. Cut CI pipeline runtime by 60 percent, "
                        "migrated 30 production services to Kubernetes, and owned "
                        "reliability for an estate of 40 services across two organisations."},
    "experience": [
        {"company": "Acme Cloud", "role": "Senior Platform Engineer",
         "dates": "Mar 2022 - Present", "bullets": [
            "Reduced CI pipeline runtime from 22 minutes to 9 minutes.",
            "Built Terraform modules adopted by 8 engineering teams.",
            "Automated certificate rotation, eliminating 12 incidents per year.",
            "Led the on-call rotation covering an estate of 40 services.",
            "Introduced progressive delivery, cutting failed releases by 35 percent.",
            "Consolidated 5 alerting systems into 1 on-call workflow.",
            "Produced an internal platform guide used by 60 engineers."]},
        {"company": "Bharat Systems", "role": "Platform Engineer",
         "dates": "Jun 2019 - Feb 2022", "bullets": [
            "Migrated 30 services from EC2 to EKS with zero customer downtime.",
            "Delivered Prometheus dashboards adopted by 5 product teams.",
            "Reduced cloud spend by 18 percent through rightsizing and scheduling.",
            "Mentored 4 junior engineers through their first production launches.",
            "Standardised 12 repositories onto a shared CI template.",
            "Resolved a recurring outage that had cost 3 hours of downtime monthly."]},
        {"company": "Nexus Retail", "role": "Infrastructure Engineer",
         "dates": "Jul 2017 - May 2019", "bullets": [
            "Rearchitected the deployment pipeline, releasing 4 times per week instead of monthly.",
            "Consolidated 9 legacy virtual machines onto 2 managed clusters.",
            "Produced 15 runbooks that cut mean time to recovery by 40 percent.",
            "Negotiated a 22 percent reduction in annual hosting costs.",
            "Coached 6 developers to run their own deployments safely.",
            "Established a change-review process covering 100 percent of releases."]},
    ],
    "skills": [
        {"label": "Infrastructure",
         "items": ["Terraform", "Kubernetes", "AWS", "Docker", "ArgoCD", "Helm"]},
        {"label": "Languages", "items": ["Python", "Go", "Bash", "SQL"]},
        {"label": "Observability",
         "items": ["Prometheus", "Grafana", "OpenTelemetry", "Loki"]},
    ],
    "education": [{"school": "Savitribai Phule Pune University",
                   "degree": "B.E. Computer Engineering", "dates": "2019"}],
    "projects": [{"name": "kubecost-lite", "description":
                  "Open-source cost attribution for Kubernetes namespaces, "
                  "adopted by 3 companies and 200 GitHub stars.", "tech": ["Go","Kubernetes"]}],
})

# Each patch introduces exactly ONE finding against the clean BASE; a test
# enforces that, so a case cannot silently start measuring two things at once.
CASES: list[dict] = [
    # ------------------------------------------- fixable from existing facts
    {
        # The original text said "CI pipelines CUT by 60 percent" and
        # "30 services MOVED to Kubernetes". It only raised
        # summary.no_action_verb because ACTION_VERBS was past-tense-only and
        # knew neither word -- the case was valid only because of a bug in the
        # very thing it was testing. Reworded so it genuinely contains no
        # action verb: same numbers, same intent, same single finding.
        "id": "summary_weak_opening",
        "code": "summary.no_action_verb",
        "target_ref": "summary.text",
        "patch": {"summary.text":
                  "Responsible for cloud infrastructure over 6 years, including "
                  "CI pipelines 60 percent faster, 30 services on Kubernetes, "
                  "and reliability for an estate of 40 services."},
        "expect": "fix",
        "why": "opens with a passive phrase; the facts needed are all present",
    },
    {
        "id": "summary_too_short",
        "code": "summary.too_short",
        "target_ref": "summary.text",
        "patch": {"summary.text": "Built platforms for 6 years."},
        "expect": "fix",
        "why": "the experience section supplies plenty of material to expand from",
    },
    {
        "id": "summary_absent",
        "code": "summary.missing",
        "target_ref": "summary.text",
        "patch": {"summary.text": ""},
        "expect": "fix",
        "why": "compose path: the summary must be built from the experience below it",
    },
    {
        "id": "bullet_weak_verb",
        "code": "experience.weak_verbs",
        "target_ref": "exp_0.bullet_0",
        "patch": {"exp_0.bullet_0":
                  "Was responsible for reducing CI pipeline runtime from 22 minutes "
                  "to 9 minutes."},
        "expect": "fix",
        "why": "reordering to lead with the verb needs no new information",
    },
    {
        "id": "bullet_weak_verb_second_role",
        "code": "experience.weak_verbs",
        "target_ref": "exp_1.bullet_0",
        "patch": {"exp_1.bullet_0":
                  "Was responsible for moving 30 services from EC2 to EKS "
                  "with zero customer downtime."},
        "expect": "fix",
        "why": ("same fault in a different entry -- the fix must not be "
                "position-specific. Uses the SAME weak-verb construction as "
                "bullet_weak_verb so position is the only variable."),
        # This case used to read "Involved in moving ...", which was a
        # control failure: it varied position AND ownership. "Was responsible
        # for X" asserts ownership, so "Moved X" preserves it; "Involved in
        # X" asserts only participation, so ANY strong verb upgrades the
        # claim -- which the critic is built to reject, and did, in three
        # consecutive live runs on two different models. The case was
        # unsatisfiable: the writer could not fix the weak verb without
        # overstating the candidate's role. The critic was right every time
        # and the corpus was wrong.
    },
    {
        "id": "bullet_overlong",
        "code": "format.overlong_bullets",
        "target_ref": "exp_0.bullet_1",
        "patch": {"exp_0.bullet_1":
                  "Built a comprehensive and highly reusable library of Terraform "
                  "modules which was subsequently adopted by 8 separate engineering "
                  "teams across the wider platform organisation, and which went on to "
                  "become the standard approach for provisioning new infrastructure "
                  "in essentially every part of the business going forward."},
        "expect": "fix",
        "why": "shortening is pure subtraction; nothing needs inventing",
    },
    {
        "id": "bullet_overlong_and_padded",
        "code": "format.overlong_bullets",
        "target_ref": "exp_2.bullet_0",
        "patch": {"exp_2.bullet_0":
                  "Rearchitected the deployment pipeline together with a number of "
                  "different stakeholders over many months in such a way that it "
                  "became possible to release approximately 4 times per week rather "
                  "than only once per month as had previously been the case for the "
                  "preceding several years of the project's operation."},
        "expect": "fix",
        "why": "padding and length together; the 4-per-week fact must survive",
    },

    # --------------------------------- NOT fixable without inventing a fact
    {
        "id": "summary_no_metric",
        "code": "summary.no_metric",
        "target_ref": "summary.text",
        "patch": {"summary.text":
                  "Senior platform engineer who has built and operated cloud "
                  "infrastructure, led incident response across multiple delivery "
                  "teams, mentored junior engineers, and steadily reduced operational "
                  "toil throughout a career spanning several large organisations."},
        "expect": "decline",
        "why": "no number exists to surface; the only way to 'fix' this is to make "
               "one up. This is the exact case that produced the 422 bug.",
    },
    {
        "id": "bullet_unquantified",
        "code": "experience.unquantified_bullets",
        "target_ref": "exp_0.bullet_3",
        "patch": {"exp_0.bullet_3":
                  "Led the on-call rotation for the production estate."},
        "expect": "decline",
        "why": "the estate size was removed; re-adding it would be invention",
    },
    {
        "id": "bullet_unquantified_vague",
        "code": "experience.unquantified_bullets",
        "target_ref": "exp_1.bullet_2",
        "patch": {"exp_1.bullet_2":
                  "Reduced cloud spend through rightsizing and scheduling."},
        "expect": "decline",
        "why": "a real improvement with no stated magnitude -- inventing one is the "
               "single most tempting fabrication in resume writing",
    },
]


def resume_for(case: dict) -> dict:
    """BASE with this case's patch applied."""
    import copy

    data = copy.deepcopy(BASE)
    for ref, text in case["patch"].items():
        data = resume_ops.apply_patch(data, ref, text)
    return data


def to_fix() -> list[dict]:
    return [c for c in CASES if c["expect"] == "fix"]


def to_decline() -> list[dict]:
    return [c for c in CASES if c["expect"] == "decline"]
