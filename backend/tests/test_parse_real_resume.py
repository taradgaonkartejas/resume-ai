"""Three parser bugs found against a real, user-supplied resume.

Every layout below is copied verbatim from the reported PDF. They are kept
as text rather than as binary fixtures because all three bugs live in
`structure_text`, not in PDF extraction -- and because the same resume
exported through a *different* template parsed perfectly, which is what
isolated each cause.

The contrast matters, so both layouts are tested: the one that failed and
the one that always worked. A fix that repairs the first by breaking the
second is not a fix.
"""

from app.services.parsing import structure_text

# The layout that FAILED: a product subtitle under the header, unlabelled
# grades on their own line, and wrapped achievement bullets.
FAILING = """Tejas Suraj Taradgaonkar
Full-Stack Software Engineer
taradgaonkartejas@gmail.com | +91 8208300420 | GitHub | LinkedIn
EXPERIENCE
Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present
Scan to Model (S2M) — global point-cloud platform for surveying & civil engineering
• Architected a RabbitMQ-based microservices pipeline that lifted throughput to 10 jobs/hour.
• Improved processing performance for multi-gigabyte point-cloud datasets by 400%.
EDUCATION
Post Graduate Diploma in Advanced Computing (PG-DAC) | CDAC, Pune 2024
73%
B.E. (Mechanical) | Vishwakarma Institute of Technology (VIT), Pune 2020
CGPA: 7.79
XII (HSC) | Modern College of Science, Commerce & Arts, Pune 2016
67.85%
LEADERSHIP & ACHIEVEMENTS
• Founder, Kalarpan (2019–present) — Founded and run a charity initiative that funds donations
through staged theatrical performances; own everything from production to fundraising.
• Multiple-time winner, inter-collegiate engineering design events — Texephyr'19, Credenz'18,
Gandharv'19, Innovation'19.
"""

# The layout that ALWAYS WORKED. Same person, same employer, no subtitle
# line, labelled grades, properly separated bullets.
WORKING = """Tejas Suraj Taradgaonkar
Full-Stack Software Engineer
taradgaonkartejas@gmail.com | +91 8208300420
EXPERIENCE
Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present
• Architected a RabbitMQ-based microservices pipeline to lift throughput to 10 jobs/hour.
• Leveraged analytical skills to improve performance by 400%.
EDUCATION
Post Graduate Diploma in Advanced Computing (PG-DAC) — CDAC, Pune 2024 | Pune
Score: 73%
B.E. (Mechanical) — Vishwakarma Institute of Technology (VIT) 2020 | Pune
CGPA: 7.79
"""


# ------------------------------------------------- bug 1: product subtitle
def test_a_product_subtitle_does_not_replace_the_employer():
    """Before: company became "Scan to Model (S2M)".

    The subtitle's em-dash was read as a company/role separator, so it
    opened a new entry -- and because that entry had no bullets yet it
    silently overwrote the real one.
    """
    data = structure_text(FAILING)
    assert len(data["experience"]) == 1
    entry = data["experience"][0]
    assert entry["company"] == "Ceinsys Tech Ltd."
    assert entry["role"] == "Software Engineer"


def test_the_dates_survive_the_subtitle():
    """The overwrite took the dates with it, which cost 5 points.

    A missing-dates finding then appeared for an entry whose dates were
    printed on the page -- the parser inventing a problem it had caused.
    """
    assert data_dates(FAILING) == "Apr 2024 – Present"


def test_the_subtitle_text_is_kept():
    """Never discard a line the candidate wrote.

    It describes the work, so it is filed as content rather than thrown
    away or promoted to an employer.
    """
    bullets = structure_text(FAILING)["experience"][0]["bullets"]
    assert any("Scan to Model" in b for b in bullets)


def test_a_real_second_employer_still_opens_an_entry():
    """Guard the guard.

    The fix keys on "the open entry has no bullets yet". A genuine second
    employer always arrives after bullets, so it must be unaffected --
    otherwise the fix would merge every job into the first one.
    """
    data = structure_text(
        "EXPERIENCE\n"
        "Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present\n"
        "• Built the ingestion pipeline.\n"
        "Junior Engineer | Older Corp. Jan 2021 – Mar 2024\n"
        "• Maintained the billing service.\n"
    )
    assert len(data["experience"]) == 2
    assert data["experience"][1]["company"] == "Older Corp."


def data_dates(raw: str) -> str:
    return structure_text(raw)["experience"][0]["dates"]


# ----------------------------------------------------- bug 2: bare grades
def test_an_unlabelled_grade_is_not_a_school():
    """Before: schools called "73%" and "67.85%" appeared in the list.

    Those rows reach the export, so a resume sent to an employer listed a
    percentage as an institution.
    """
    schools = [e["school"] for e in structure_text(FAILING)["education"]]
    assert "73%" not in schools
    assert "67.85%" not in schools
    assert len(schools) == 3


def test_an_unlabelled_grade_attaches_to_the_entry_above():
    edu = structure_text(FAILING)["education"]
    by_degree = {e["degree"]: e for e in edu}
    assert by_degree["Post Graduate Diploma in Advanced Computing (PG-DAC)"]["grade"] == "73%"
    assert by_degree["XII (HSC)"]["grade"] == "67.85%"


def test_a_labelled_grade_still_works():
    edu = structure_text(FAILING)["education"]
    assert any(e["grade"] == "CGPA 7.79" for e in edu)


def test_a_bare_year_is_not_mistaken_for_a_grade():
    """The inverse bug, which the narrow regex exists to prevent.

    "2024" alone is a date. Accepting bare integers as grades would turn
    every year-on-its-own-line into a percentage.
    """
    data = structure_text(
        "EDUCATION\nB.E. (Mechanical) | Some Institute\n2020\n"
    )
    assert data["education"][0]["grade"] == ""


# ------------------------------------------------- bug 3: wrapped extras
def test_a_wrapped_achievement_does_not_become_its_own_entry():
    """Before: "through staged theatrical performances; own everything..."
    was listed as a separate achievement."""
    extras = structure_text(FAILING)["extras"]
    leadership = [x for x in extras if "Leadership" in x["title"]][0]
    primaries = [e["primary"] for e in leadership["entries"]]
    assert not any(p.startswith("through staged") for p in primaries)
    assert not any(p.startswith("Gandharv") for p in primaries)


def test_the_real_achievements_are_all_present():
    extras = structure_text(FAILING)["extras"]
    leadership = [x for x in extras if "Leadership" in x["title"]][0]
    primaries = [e["primary"] for e in leadership["entries"]]
    assert primaries == ["Founder", "Multiple-time winner"]


def test_a_line_after_a_comma_is_treated_as_a_wrap():
    """"Gandharv'19" opens uppercase, so the lowercase rule cannot see it.

    The reliable signal is the PREVIOUS line ending mid-list: no heading
    has ever followed a line ending in a comma.
    """
    extras = structure_text(FAILING)["extras"]
    leadership = [x for x in extras if "Leadership" in x["title"]][0]
    winner = [e for e in leadership["entries"] if e["primary"] == "Multiple-time winner"][0]
    assert "Gandharv" in winner["secondary"]


# --------------------------------------------------------- no regression
def test_the_layout_that_always_worked_still_works():
    data = structure_text(WORKING)
    entry = data["experience"][0]
    assert entry["company"] == "Ceinsys Tech Ltd."
    assert entry["role"] == "Software Engineer"
    assert entry["dates"] == "Apr 2024 – Present"
    assert len(entry["bullets"]) == 2
    assert len(data["education"]) == 2
    assert data["education"][0]["grade"] == "73%"


def test_both_layouts_agree_on_the_employer():
    """The whole point: the same job, described two ways, parses the same."""
    a = structure_text(FAILING)["experience"][0]
    b = structure_text(WORKING)["experience"][0]
    assert (a["company"], a["role"], a["dates"]) == (b["company"], b["role"], b["dates"])
