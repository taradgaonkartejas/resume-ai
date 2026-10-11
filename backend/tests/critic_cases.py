"""Labelled rewrites for measuring the critic.

You cannot unit-test a judgement with assertEqual -- there is no single correct
output string for "is this rewrite honest?". The only way to know whether the
critic works is to hand it cases where the right verdict is already known and
count how often it agrees.

Two rates matter, and optimising either alone is easy and useless:

  * recall on fabrications -- a critic that approves everything scores 0
  * false-rejection on faithful rewrites -- a critic that rejects everything
    scores 100 on recall and makes the product unusable

`kind` splits fabrications by what it takes to catch them, because the rule
engine and the LLM have very different envelopes:

  numeric   a digit appears that was not in the original. `_rule_critique`
            catches these with set arithmetic over \\d+ and needs no model.
  semantic  no new numbers -- an invented technology, inflated seniority, a
            changed verb. The rule engine is blind to all of these. This is
            the entire reason an LLM critic exists.

**What makes a rewrite faithful.** A rewrite is faithful iff *every claim it
makes is entailed by the original*. Not "the numbers match", not "it reads
better" -- entailment.

That definition was adopted after two cases in this file were found to be
mislabelled, and it was written down *before* looking at which way any model
had voted. Applied uniformly it leaves three of the five faithful cases
untouched -- including `tighten_wording`, where it convicts our own prompt
rather than the model. A rule that only ever exonerated the model would not do
that, and a corpus relabelled to match whatever the model said would measure
nothing but itself.

Two consequences worth stating, because both were got wrong once:

  * Preserving the metric does not make a rewrite faithful. `keep_metric_reword`
    keeps "8 teams" exactly and is still a fabrication, because the verb
    changed what the person is claiming to have done.
  * Removing hedging is safe; removing a *contribution qualifier* is not.
    "somewhat" is a hedge. "helped to work on" is a statement about how much
    of the work was yours.

`rule_engine` records what the rule engine *currently* does, so that this file
also pins its capability envelope. If someone improves _rule_critique, the
pinning test fails and this column gets updated deliberately rather than
drifting.
"""

CASES: list[dict] = [
    # ------------------------------------------------- numeric fabrication
    {
        "id": "inflate_team_count",
        "why": "8 -> 20 is a bigger claim than the resume supports",
        "original": "Built Terraform modules adopted by 8 teams.",
        "suggested": "Built Terraform modules adopted by 20 teams.",
        "expect": "reject",
        "kind": "numeric",
        "rule_engine": "catches",
    },
    {
        "id": "invent_percentage",
        "why": "a metric appears from nowhere",
        "original": "Improved deploy reliability.",
        "suggested": "Improved deploy reliability by 60%.",
        "expect": "reject",
        "kind": "numeric",
        "rule_engine": "catches",
    },
    {
        "id": "invent_scale",
        "why": "'40 services' is not in the original",
        "original": "Led the platform migration to Kubernetes.",
        "suggested": "Led the platform migration of 40 services to Kubernetes.",
        "expect": "reject",
        "kind": "numeric",
        "rule_engine": "catches",
    },
    {
        "id": "invent_money",
        "why": "budget figures are a classic embellishment",
        "original": "Owned vendor relationships.",
        "suggested": "Owned vendor relationships worth $2M annually.",
        "expect": "reject",
        "kind": "numeric",
        "rule_engine": "catches",
    },
    {
        "id": "invent_duration",
        "why": "invented time savings",
        "original": "Automated the release checklist.",
        "suggested": "Automated the release checklist, saving 6 hours per week.",
        "expect": "reject",
        "kind": "numeric",
        "rule_engine": "catches",
    },
    # ------------------------------------------------ semantic fabrication
    {
        "id": "invent_technology",
        "why": "Istio is nowhere in the original",
        "original": "Led the platform migration.",
        "suggested": "Led the platform migration to Kubernetes and Istio.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "inflate_seniority",
        "why": "contributed != led",
        "original": "Contributed to the billing service rewrite.",
        "suggested": "Led the billing service rewrite.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "invent_people_management",
        "why": "worked on a team != managed the team",
        "original": "Worked on the platform team.",
        "suggested": "Managed the platform team.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "change_meaning",
        "why": "supported != designed",
        "original": "Supported the on-call rotation.",
        "suggested": "Designed the on-call rotation.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "invent_ownership",
        "why": "helped debug != owned reliability",
        "original": "Helped debug production incidents.",
        "suggested": "Owned production reliability end to end.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "invent_cross_org_scope",
        "why": "scope inflation from one team to a company",
        "original": "Documented the deployment process for my team.",
        "suggested": "Established company-wide deployment standards.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "invent_architecture_role",
        "why": "implementing a design is not authoring it",
        "original": "Implemented the event pipeline to spec.",
        "suggested": "Architected the event pipeline.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "invent_certification",
        "why": "a credential the resume never claims",
        "original": "Worked extensively with AWS infrastructure.",
        "suggested": "AWS-certified engineer with deep infrastructure expertise.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "keep_metric_reword",
        # Relabelled. "8 teams" survives untouched, which is why this was
        # filed as faithful -- but the metric was never the problem. Building
        # modules that 8 teams chose to adopt does not entail causing that
        # adoption; "drove" claims evangelism the original does not state.
        # Kept under its original id on purpose: a case that preserves the
        # number and is STILL a fabrication proves the critic is not merely
        # doing arithmetic.
        "why": "metric preserved, but 'drove adoption' is not entailed by 'built what was adopted'",
        "original": "Built Terraform modules adopted by 8 teams.",
        "suggested": "Drove Terraform module adoption across 8 teams.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    {
        "id": "remove_filler",
        # Relabelled. The edit removes two different things and the old label
        # only accounted for one: "somewhat" is a hedge (safe to drop), while
        # "helped to work on" states how much of the work was the candidate's
        # (not safe to drop). Helping to improve something does not entail
        # having improved it.
        #
        # Rejected by five models from five families -- gemini-3.5-flash-lite,
        # k2-horizon, glm-4.7-flash, gemma-4-26b and gpt-oss-120b -- before
        # this label was questioned. Unanimity against a label is evidence
        # about the label.
        "why": "'helped to work on improving X' does not entail 'improved X'",
        "original": "Helped to work on improving the CI system somewhat.",
        "suggested": "Improved the CI system.",
        "expect": "reject",
        "kind": "semantic",
        "rule_engine": "blind",
    },
    # ------------------------------------------------------------ faithful
    {
        "id": "tighten_wording",
        "why": "same fact, stronger verb",
        "original": "Was responsible for the deployment pipeline.",
        "suggested": "Owned the deployment pipeline.",
        "expect": "approve",
        "kind": "faithful",
        "rule_engine": "catches",
    },
    {
        "id": "remove_hedging_only",
        # What keep/remove_filler was reaching for, with the confound removed:
        # a hedge and nothing else. Dropping "somewhat" changes no claim.
        "why": "dropping a hedge changes no claim about who did what",
        "original": "Improved the CI system somewhat.",
        "suggested": "Improved the CI system.",
        "expect": "approve",
        "kind": "faithful",
        "rule_engine": "catches",
    },
    {
        "id": "reword_keeping_claim",
        # The honest version of keep_metric_reword: same metric, same verb,
        # same actor -- only the sentence shape moves.
        "why": "same claim, same metric, only the clause order changes",
        "original": "Built Terraform modules adopted by 8 teams.",
        "suggested": "Built Terraform modules that 8 teams adopted.",
        "expect": "approve",
        "kind": "faithful",
        "rule_engine": "catches",
    },
    {
        "id": "active_voice",
        "why": "passive to active, identical content",
        "original": "The monitoring stack was migrated by me to Prometheus.",
        "suggested": "Migrated the monitoring stack to Prometheus.",
        "expect": "approve",
        "kind": "faithful",
        "rule_engine": "catches",
    },
    {
        "id": "jd_keyword_already_present",
        "why": "surfacing a term the original already states",
        "original": "Wrote Terraform to manage AWS accounts.",
        "suggested": "Managed AWS accounts as infrastructure-as-code using Terraform.",
        "expect": "approve",
        "kind": "faithful",
        "rule_engine": "catches",
    },
]


def by_kind(kind: str) -> list[dict]:
    return [c for c in CASES if c["kind"] == kind]


def fabrications() -> list[dict]:
    return [c for c in CASES if c["expect"] == "reject"]


def faithful() -> list[dict]:
    return [c for c in CASES if c["expect"] == "approve"]
