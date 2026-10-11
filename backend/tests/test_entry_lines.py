"""Education and experience entry composition, shared by all four surfaces.

The defect: `field_of_study`, `grade` and both `location` fields are in the
canonical shape and editable in the step editor, but rendered in **no**
export. The editor promised fields the output silently dropped — the same
class of bug as the DOCX dropping profile links.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.resume_ops import (
    education_lines,
    experience_lines,
    to_plain_text,
    with_location,
)

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "entries.ts"

#: Every case is run through BOTH implementations (see the parity test).
CASES: list[dict] = [
    {
        "kind": "education",
        "entry": {
            "school": "CDAC, Pune",
            "degree": "Post Graduate Diploma in Advanced Computing (PG-DAC)",
            "location": "Pune",
            "grade": "73%",
        },
        "want": [
            "Post Graduate Diploma in Advanced Computing (PG-DAC)",
            "CDAC, Pune · 73%",
        ],
    },
    {
        "kind": "education",
        "entry": {
            "school": "Vishwakarma Institute of Technology (VIT)",
            "degree": "B.E. (Mechanical)",
            "location": "Pune",
            "grade": "CGPA 7.79",
        },
        "want": [
            "B.E. (Mechanical)",
            "Vishwakarma Institute of Technology (VIT), Pune · CGPA 7.79",
        ],
    },
    {
        "kind": "education",
        "entry": {"school": "MIT", "degree": "", "field_of_study": "Physics"},
        "want": ["Physics", "MIT"],
    },
    {
        "kind": "education",
        "entry": {"school": "MIT", "degree": "BSc", "field_of_study": "Physics"},
        "want": ["BSc, Physics", "MIT"],
    },
    {
        # The field is already spelled out in the degree: do not say it twice.
        "kind": "education",
        "entry": {"school": "MIT", "degree": "BSc Physics", "field_of_study": "Physics"},
        "want": ["BSc Physics", "MIT"],
    },
    {
        # No degree at all -- the school must still headline the entry.
        "kind": "education",
        "entry": {"school": "MIT", "degree": "", "location": "Cambridge"},
        "want": ["MIT", "Cambridge"],
    },
    {
        "kind": "experience",
        "entry": {"role": "Software Engineer", "company": "Ceinsys Tech Ltd.",
                  "location": "Nagpur"},
        "want": ["Software Engineer", "Ceinsys Tech Ltd., Nagpur"],
    },
    {
        "kind": "experience",
        "entry": {"role": "", "company": "Acme", "location": "Pune"},
        "want": ["Acme, Pune", ""],
    },
]


def _run(case: dict) -> list[str]:
    fn = education_lines if case["kind"] == "education" else experience_lines
    return list(fn(case["entry"]))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["want"][0][:28])
def test_entries_compose_every_editable_field(case):
    assert _run(case) == case["want"]


def test_a_city_already_in_the_name_is_not_repeated():
    """"CDAC, Pune" with location "Pune" must not render "CDAC, Pune, Pune".

    Indian institutions are routinely written with their city in the name, so
    this is the common case rather than a corner one.
    """
    assert with_location("CDAC, Pune", "Pune") == "CDAC, Pune"
    assert with_location("Acme Corp", "Pune") == "Acme Corp, Pune"
    assert with_location("", "Pune") == "Pune"
    assert with_location("Acme", "") == "Acme"


def test_the_inline_template_puts_role_and_company_on_one_line():
    assert experience_lines(
        {"role": "Eng", "company": "Acme", "location": "Pune"}, inline=True
    ) == ("Eng - Acme, Pune", "")


# ------------------------------------------------------- the fields reach TXT
def _doc() -> dict:
    return {
        "contact": {"name": "Jane", "email": "", "phone": "", "location": "", "links": []},
        "experience": [
            {"role": "Engineer", "company": "Acme", "location": "Pune",
             "dates": "2020", "bullets": ["Did a thing."]}
        ],
        "education": [
            {"school": "VIT", "degree": "B.E.", "location": "Pune",
             "grade": "CGPA 7.79", "dates": "2020"}
        ],
    }


def test_grade_and_location_survive_into_plain_text():
    text = to_plain_text(_doc())
    assert "CGPA 7.79" in text
    assert "VIT, Pune" in text
    assert "Acme, Pune" in text


def test_grade_and_location_survive_into_pdf_and_docx():
    import zipfile

    import pdfplumber

    from app.services.export_service import ExportService

    svc = ExportService.__new__(ExportService)
    pdf_bytes = svc._render_pdf(_doc(), None)
    with pdfplumber.open(__import__("io").BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    assert "CGPA 7.79" in text, text
    assert "VIT, Pune" in text

    docx_xml = zipfile.ZipFile(
        __import__("io").BytesIO(svc._render_docx(_doc()))
    ).read("word/document.xml").decode()
    assert "CGPA 7.79" in docx_xml
    assert "Acme, Pune" in docx_xml


# ------------------------------------------------- the TS/Python parity pin
_TS_COMPILER = (
    Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / "typescript"
)

_DRIVER = """
const ts = require(process.env.TS_PATH);
const fs = require('fs');
const src = fs.readFileSync(process.env.SRC_PATH, 'utf8');
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const mod = { exports: {} };
new Function('exports', 'module', 'require', js)(mod.exports, mod, require);
const cases = JSON.parse(fs.readFileSync(process.env.CASES_PATH, 'utf8'));
console.log(JSON.stringify(cases.map(c =>
  c.kind === 'education' ? mod.exports.educationLines(c.entry)
                         : mod.exports.experienceLines(c.entry))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.skipif(
    not _TS_COMPILER.exists(), reason="frontend node_modules not installed"
)
def test_the_typescript_formatters_agree_with_the_python_ones(tmp_path):
    """The preview exists to show what the PDF will contain.

    Two hand-written copies of one composition rule drift, and the symptom is
    a preview that disagrees with the downloaded file -- the single bug a
    preview is supposed to make impossible. So the real TypeScript is
    compiled with the project's own compiler and run against the same cases.
    (Stripping the types with regexes was tried first and broke on a return
    annotation; a parity check that cannot parse its own subject is worse
    than none, because it fails for reasons unrelated to parity.)
    """
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(CASES), encoding="utf-8")
    proc = subprocess.run(
        ["node", "-e", _DRIVER],
        capture_output=True, text=True, timeout=120,
        env={
            **os.environ,
            "TS_PATH": str(_TS_COMPILER),
            "SRC_PATH": str(_TS),
            "CASES_PATH": str(cases_file),
        },
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    want = [_run(c) for c in CASES]
    assert got == want, (
        "frontend/src/lib/entries.ts disagrees with resume_ops:\n"
        + "\n".join(
            f"  {c['kind']}: TS={g!r} Python={w!r}"
            for c, g, w in zip(CASES, got, want, strict=True)
            if g != w
        )
    )
