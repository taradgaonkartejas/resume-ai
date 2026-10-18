"""A project heading must be a heading.

Two defects, both found by re-parsing the user's own resumes and both
visible in the product as a wrong PLACEMENT on a tailor card:

  P1  "Tech Stack: Next.js, Aceternity UI, Node Js/Express Js, FastAPI,
       PostgreSQL, HubSpot API, OpenAI API, Nodemailer"
      became a project NAME, in two of the three real resumes. The tailor
      then rendered a card whose placement was the entire technology list --
      which is exactly the "Java (Spring Boot), React.js, MySQL" placement
      in the screenshot that started this.

  P2  "200%." became a project. It is the tail of a sentence that wrapped
      ("...improving throughput by 200%."), and because it starts with a
      digit rather than a lowercase letter `_is_bullet_continuation` did not
      claim it, so it fell through to the header branch.

`placement_for` and `iter_bullets` were never at fault: they faithfully
reported a name the parser had got wrong.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import resume_ops
from app.services.parsing import (
    _is_bullet_continuation,
    _looks_like_entry_header,
    extract_text,
    structure_text,
)

UPLOADS = pathlib.Path("/home/user/uploads")
REAL_PDFS = sorted(UPLOADS.glob("*.pdf")) if UPLOADS.exists() else []


def _parse(pdf: pathlib.Path) -> dict:
    return structure_text(extract_text(pdf.name, pdf.read_bytes()))


# --------------------------------------------------------------- unit rules
class TestTheHeaderShapeRule:
    @pytest.mark.parametrize(
        "line",
        [
            "Outreach AI (Auto-Pilot Sales Assistant)",
            "Bike Point",
            # A trailing full stop is NOT disqualifying. A "no terminal
            # period" rule would have been the obvious fix and would have
            # broken every "Ltd."-suffixed employer in the corpus.
            "Ceinsys Tech Ltd.",
            "Software Engineer | Ceinsys Tech Ltd.",
        ],
    )
    def test_real_headings_are_accepted(self, line):
        assert _looks_like_entry_header(line)

    @pytest.mark.parametrize("line", ["200%.", "40%", "1,250", "— 200%.", "..."])
    def test_wordless_fragments_are_rejected(self, line):
        assert not _looks_like_entry_header(line)

    def test_a_single_letter_is_not_a_word(self):
        """Two characters is the threshold; "x" alone is not a heading."""
        assert not _looks_like_entry_header("x 200%")


class TestWhyTheExistingGuardMissedIt:
    def test_continuation_detection_does_not_claim_a_digit_leading_line(self):
        """Documents the gap rather than assuming it.

        `_is_bullet_continuation` merges on strong evidence only -- a
        lowercase start, or indentation without header structure. "200%."
        has neither, which is precisely why a second rule was needed instead
        of loosening this one. Loosening it would risk destroying real
        entries, which its docstring warns about.
        """
        entry = {"company": "", "role": "", "dates": "", "bullets": ["Improved throughput"]}
        assert _is_bullet_continuation("200%.", entry) is False


# ------------------------------------------------------------- synthetic end
class TestStructuring:
    def _text(self, body: str) -> str:
        return "PROJECTS\n" + body

    def test_a_tech_stack_line_does_not_open_a_project(self):
        data = structure_text(
            self._text(
                "Outreach AI (Auto-Pilot Sales Assistant)\n"
                "Tech Stack: Next.js, FastAPI, PostgreSQL\n"
                "- Built a HubSpot-backed sequencing engine\n"
            )
        )
        names = [p["name"] for p in data["projects"]]
        assert names == ["Outreach AI (Auto-Pilot Sales Assistant)"]

    def test_the_tech_stack_text_is_kept_not_discarded(self):
        """It is content the candidate wrote. Filing it one level down beats
        deleting it -- the same call the product/project-subtitle rule makes."""
        data = structure_text(
            self._text(
                "Outreach AI\nTech Stack: Next.js, FastAPI\n- Built the engine\n"
            )
        )
        joined = " ".join(data["projects"][0]["bullets"])
        assert "Next.js" in joined

    @pytest.mark.parametrize(
        "label",
        ["Tech Stack:", "Stack:", "Tools:", "Technologies:", "Built with:", "Frameworks:"],
    )
    def test_each_label_form_is_recognised(self, label):
        data = structure_text(
            self._text(f"Outreach AI\n{label} Next.js, FastAPI\n- Built the engine\n")
        )
        assert [p["name"] for p in data["projects"]] == ["Outreach AI"]

    def test_a_wrapped_percentage_rejoins_its_bullet(self):
        data = structure_text(
            self._text(
                "Outreach AI\n"
                "- Improved reply rates and lifted throughput by\n"
                "200%.\n"
            )
        )
        assert [p["name"] for p in data["projects"]] == ["Outreach AI"]
        assert "200%." in " ".join(data["projects"][0]["bullets"])

    def test_two_real_projects_still_split(self):
        """The guard must not weld separate projects together."""
        data = structure_text(
            self._text(
                "Outreach AI\n- Built the engine\nBike Point\n- Built the booking flow\n"
            )
        )
        assert [p["name"] for p in data["projects"]] == ["Outreach AI", "Bike Point"]


# ------------------------------------------------------------ the real files
@pytest.mark.skipif(not REAL_PDFS, reason="real resumes not present")
class TestTheRealResumes:
    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_no_project_is_named_after_a_tech_stack(self, pdf):
        data = _parse(pdf)
        for project in data.get("projects", []):
            name = project.get("name", "")
            assert not name.lower().startswith("tech stack"), name

    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_every_project_name_contains_a_word(self, pdf):
        data = _parse(pdf)
        for project in data.get("projects", []):
            assert _looks_like_entry_header(project.get("name", "")), project

    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_no_placement_is_a_technology_list(self, pdf):
        """The user-visible symptom, asserted where the user saw it."""
        data = _parse(pdf)
        for _ref, placement, _text in resume_ops.iter_bullets(data):
            assert not placement.lower().startswith("tech stack"), placement
            assert len(placement) < 80, f"placement reads as prose: {placement!r}"

    @pytest.mark.parametrize("pdf", REAL_PDFS, ids=lambda p: p.name)
    def test_the_expected_projects_survive(self, pdf):
        """Both real projects are still found -- the fix removed the junk
        entries without eating the genuine ones."""
        names = " | ".join(p.get("name", "") for p in _parse(pdf).get("projects", []))
        assert "Outreach AI" in names
        assert "Bike Point" in names
