"""Company/role headers: sub-brands and ambiguous orientation.

Both defects were found by re-parsing the user's own resumes, and both
surfaced as a wrong PLACEMENT on a tailor card.

P3  "Software Engineer | Ceinsys Tech Ltd. — Scan to Model (S2M)"
    put the product into the employer field, giving the placement
    "Ceinsys Tech Ltd. — Scan to Model (S2M) — Software Engineer". Because
    `placement_for` joins company and role with that SAME " — ", the result
    carried three separator segments and could be neither read nor split.

P4  "Animator | Vedantu" parsed as company="Animator", role="Vedantu".
    `_order_pair` scores each side and keeps the given order on a tie, which
    is correct in isolation -- guessing is how a good entry gets destroyed --
    but neither "Animator" nor "Vedantu" is in the job-title or
    company-suffix vocabulary, and no vocabulary will ever cover every job.

    The missing evidence is in the DOCUMENT, not the line: the same resume
    resolves "Software Engineer | Ceinsys Tech Ltd. — ..." unambiguously as
    role-first, so the ambiguous row is role-first too.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import resume_ops
from app.services.parsing import (
    _COMPANY_RE,
    _ROLE_RE,
    _pair_lean,
    _resolve_ambiguous_orientation,
    extract_text,
    structure_text,
)

UPLOADS = pathlib.Path("/home/user/uploads")
REAL_PDFS = sorted(UPLOADS.glob("*.pdf")) if UPLOADS.exists() else []


def _exp(body: str) -> list[dict]:
    return structure_text("EXPERIENCE\n" + body).get("experience", [])


# ----------------------------------------------------------------- P3
class TestSubBrandSplitting:
    def test_the_product_leaves_the_employer_field(self):
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. — Scan to Model (S2M) Apr 2024 – Present\n"
            "Point-cloud platform for surveying, used globally.\n"
            "- Built a map library\n"
        )
        assert entries[0]["company"] == "Ceinsys Tech Ltd."
        assert entries[0]["role"] == "Software Engineer"

    def test_the_placement_has_exactly_one_separator(self):
        """The user-visible symptom. `placement_for` joins with " — ", so a
        company containing one produced a three-segment label."""
        data = structure_text(
            "EXPERIENCE\n"
            "Software Engineer | Ceinsys Tech Ltd. — Scan to Model (S2M) Apr 2024 – Present\n"
            "Point-cloud platform for surveying.\n"
            "- Built a map library\n"
        )
        placement = resume_ops.placement_for(data, "exp_0.bullet_0")
        assert placement == "Ceinsys Tech Ltd. — Software Engineer"
        assert placement.count(" — ") == 1

    def test_the_sub_brand_is_not_discarded(self):
        """It is the candidate's text. It rejoins the description line that
        follows, which is how the resume actually reads."""
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. — Scan to Model (S2M) Apr 2024 – Present\n"
            "Point-cloud platform for surveying.\n"
            "- Built a map library\n"
        )
        assert "Scan to Model (S2M)" in entries[0]["bullets"][0]
        assert "Point-cloud platform" in entries[0]["bullets"][0]

    def test_a_sub_brand_with_no_description_still_survives(self):
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. — Scan to Model (S2M) Apr 2024 – Present\n"
            "- Built a map library\n"
        )
        assert entries[0]["company"] == "Ceinsys Tech Ltd."
        assert any("Scan to Model" in b for b in entries[0]["bullets"])

    def test_a_hyphenated_company_is_not_split(self):
        """Only a SPACED em/en dash separates a sub-brand. Splitting on a
        plain hyphen would cut "Hewlett-Packard" in half."""
        entries = _exp("Engineer | Hewlett-Packard Ltd 2020 – 2023\n- Did the work\n")
        assert entries[0]["company"] == "Hewlett-Packard Ltd"


# ----------------------------------------------------------------- P4
class TestOrientationFromTheDocument:
    def test_the_tie_is_settled_by_the_confident_row(self):
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present\n"
            "- Built a map library\n"
            "Animator | Vedantu 2020 – 2023\n"
            "- Created educational animation\n"
        )
        assert [(e["company"], e["role"]) for e in entries] == [
            ("Ceinsys Tech Ltd.", "Software Engineer"),
            ("Vedantu", "Animator"),
        ]

    def test_a_company_first_document_settles_ties_the_other_way(self):
        """The rule follows the document, it does not impose a house style."""
        entries = _exp(
            "Ceinsys Tech Ltd. | Software Engineer Apr 2024 – Present\n"
            "- Built a map library\n"
            "Vedantu | Animator 2020 – 2023\n"
            "- Created educational animation\n"
        )
        assert [(e["company"], e["role"]) for e in entries] == [
            ("Ceinsys Tech Ltd.", "Software Engineer"),
            ("Vedantu", "Animator"),
        ]

    def test_an_unambiguous_row_is_never_overridden(self):
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present\n- Did the work\n"
        )
        assert entries[0]["company"] == "Ceinsys Tech Ltd."

    def test_no_evidence_anywhere_leaves_the_order_alone(self):
        """With nothing to learn from, guessing is worse than not guessing --
        the conservatism `_order_pair` was written with is preserved."""
        entries = _exp("Animator | Vedantu 2020 – 2023\n- Created animation\n")
        assert entries[0]["company"] == "Animator"

    def test_an_inconsistent_document_is_left_alone(self):
        rows = [
            {"company": "Acme Ltd", "role": "Engineer", "_tied": False, "_role_first": False},
            {"company": "Engineer", "role": "Beta Ltd", "_tied": False, "_role_first": True},
            {"company": "Animator", "role": "Vedantu", "_tied": True, "_role_first": True},
        ]
        _resolve_ambiguous_orientation(rows)
        assert rows[2]["company"] == "Animator"  # untouched

    def test_bookkeeping_keys_never_escape(self):
        """`_tied` and `_role_first` are internal; they must not reach the
        structured data the API serves or the frontend renders."""
        entries = _exp(
            "Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present\n- Did the work\n"
        )
        assert not any(k.startswith("_") for k in entries[0])


class TestPairLean:
    @pytest.mark.parametrize(
        "left,right,sign",
        [
            ("Software Engineer", "Ceinsys Tech Ltd.", 1),   # swap
            ("Ceinsys Tech Ltd.", "Software Engineer", -1),  # keep
            ("Animator", "Vedantu", 0),                      # tie
        ],
    )
    def test_lean_direction(self, left, right, sign):
        lean = _pair_lean(left, right, _COMPANY_RE, _ROLE_RE)
        assert (lean > 0) == (sign == 1)
        assert (lean < 0) == (sign == -1)
        assert (lean == 0) == (sign == 0)


# ------------------------------------------------------------ real files
@pytest.mark.skipif(not REAL_PDFS, reason="real resumes not present")
class TestTheRealResumes:
    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_no_placement_has_a_doubled_separator(self, pdf):
        data = structure_text(extract_text(pdf.name, pdf.read_bytes()))
        for ref, placement, _ in resume_ops.iter_bullets(data):
            assert placement.count(" — ") <= 1, f"{ref}: {placement!r}"

    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_the_employer_is_the_employer(self, pdf):
        data = structure_text(extract_text(pdf.name, pdf.read_bytes()))
        for entry in data.get("experience", []):
            assert "Scan to Model" not in entry.get("company", "")

    def test_the_vedantu_row_is_the_right_way_round(self):
        pdf = UPLOADS / "Tejas_Taradgaonkar_Resume_node-python.pdf"
        if not pdf.exists():
            pytest.skip("node-python resume not present")
        data = structure_text(extract_text(pdf.name, pdf.read_bytes()))
        rows = [(e["company"], e["role"]) for e in data["experience"]]
        assert ("Vedantu", "Animator") in rows
