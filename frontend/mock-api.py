#!/usr/bin/env python3
"""
Throwaway mock of the 5 endpoints the upload + template flow touches.

WHY THIS EXISTS: the real FastAPI backend cannot run in this sandbox (no
conda env, no sqlalchemy, no Postgres). This mock speaks the exact shapes
from app/schemas.py so the flow can be exercised for real -- multipart
upload, inline parse, template apply -- rather than only type-checked.

It is NOT part of the app. Delete it, or keep it for offline UI work.
Run: python3 mock-api.py   (listens on :8000, which vite proxies /api to)
"""
import json
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Mirrors backend/app/services/seed.py USERS exactly -- names, titles,
# colours and count. A mock with a different roster makes the user switcher
# look right here and wrong in production.
USERS = [
    {"id": str(uuid.uuid4()), "email": "priya@example.com", "name": "Priya Sharma",
     "title": "Site Reliability Engineer", "avatar_color": "#2563eb", "chat_tokens_left": 25},
    {"id": str(uuid.uuid4()), "email": "arjun@example.com", "name": "Arjun Mehta",
     "title": "Backend Engineer", "avatar_color": "#16a34a", "chat_tokens_left": 25},
    {"id": str(uuid.uuid4()), "email": "sara@example.com", "name": "Sara Iyer",
     "title": "Product Manager", "avatar_color": "#db2777", "chat_tokens_left": 25},
    {"id": str(uuid.uuid4()), "email": "daniel@example.com", "name": "Daniel Okafor",
     "title": "Data Engineer", "avatar_color": "#ea580c", "chat_tokens_left": 25},
    {"id": str(uuid.uuid4()), "email": "lena@example.com", "name": "Lena Fischer",
     "title": "Frontend Engineer", "avatar_color": "#7c3aed", "chat_tokens_left": 25},
]

TEMPLATES = [
    {"id": str(uuid.uuid4()), "key": k, "name": n, "description": d, "design_tokens": t}
    for k, n, d, t in [
        ('modern', 'Modern', 'Accent rules and a clean left-aligned header.',
         {'font': 'sans', 'heading': 'rule', 'name_align': 'left', 'accent': '#4737ff', 'density': 'normal', 'caps': True, 'divider': False, 'header': 'stacked', 'entry': 'stacked', 'skill_columns': 2}),
        ('executive', 'Executive', 'Serif with a split header and warm copper accents.',
         {'font': 'serif', 'heading': 'rule', 'name_align': 'left', 'accent': '#A2643C', 'density': 'normal', 'caps': True, 'divider': True, 'header': 'split', 'entry': 'stacked', 'skill_columns': 3}),
        ('balanced', 'Balanced', 'Centred header with muted green section titles.',
         {'font': 'sans', 'heading': 'plain', 'name_align': 'center', 'accent': '#5F8A7D', 'density': 'normal', 'caps': False, 'divider': False, 'header': 'stacked', 'entry': 'stacked', 'skill_columns': 1}),
        ('classic', 'Classic', 'Traditional serif, centred and understated.',
         {'font': 'serif', 'heading': 'underline', 'name_align': 'center', 'accent': '', 'density': 'normal', 'caps': True, 'divider': True, 'header': 'stacked', 'entry': 'stacked', 'skill_columns': 1}),
        ('minimal', 'Minimal', 'Airy monochrome layout with generous whitespace.',
         {'font': 'sans', 'heading': 'rule', 'name_align': 'center', 'accent': '', 'density': 'airy', 'caps': True, 'divider': False, 'header': 'stacked', 'entry': 'stacked', 'skill_columns': 2}),
        ('compact', 'Compact', 'Dense single-page layout with three-column skills.',
         {'font': 'sans', 'heading': 'plain', 'name_align': 'left', 'accent': '', 'density': 'dense', 'caps': True, 'divider': True, 'header': 'split', 'entry': 'inline', 'skill_columns': 3}),
        ('technical', 'Technical', 'Bar-led headings and tight spacing for engineering roles.',
         {'font': 'sans', 'heading': 'sidebar', 'name_align': 'left', 'accent': '#3529bf', 'density': 'dense', 'caps': False, 'divider': False, 'header': 'stacked', 'entry': 'inline', 'skill_columns': 3}),
    ]
]

DEMO = {
    "contact": {"name": "Priya Sharma", "headline": "Site Reliability Engineer",
                "email": "priya@example.com",
                "phone": "+91 98765 43210", "location": "Pune, India",
                "links": ["github.com/priyasharma"]},
    "summary": {"text": "Site reliability engineer with six years running production "
                        "infrastructure for high-traffic services. Focused on reducing "
                        "operational toil through automation."},
    "experience": [
        {"company": "Acme Corp", "role": "Senior SRE", "dates": "2021 - Present",
         "bullets": ["Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
                     "Built Terraform modules adopted by 8 engineering teams",
                     "Reduced pager volume by 45% by rewriting alert rules around SLOs"]},
        {"company": "Globex", "role": "Infrastructure Engineer", "dates": "2019 - 2021",
         "bullets": ["Automated PostgreSQL failover, improving recovery time to under 2 minutes",
                     "Introduced CI pipelines that cut build times from 22 to 7 minutes"]},
    ],
    "projects": [{"name": "kubewatch",
                  "bullets": ["Open-source controller that reports drift in cluster manifests"]}],
    "education": [{"school": "COEP Pune", "degree": "B.Tech Computer Engineering", "dates": "2015 - 2019"}],
    "skills": [{"label": "Infrastructure", "items": ["Kubernetes", "Terraform", "AWS"]},
               {"label": "Languages", "items": ["Python", "Go", "Bash"]}],
}

RESUMES: dict[str, dict] = {}
_seed_id = str(uuid.uuid4())
RESUMES[_seed_id] = {
    "id": _seed_id, "user_id": USERS[0]["id"], "title": "Priya Sharma — SRE",
    "structured_data": DEMO, "storage_key": f"{USERS[0]['id']}/{_seed_id}/resume.pdf",
    "parse_status": "ready", "parse_note": "", "template_key": "modern",
    "kind": "base", "parent_id": None, "tailored_for": "",
    "version_cursor": 0, "updated_at": datetime.now(timezone.utc).isoformat(),
}

# resume_id -> latest analysis score. Absent means NEVER analysed, which the
# UI must render as "–" rather than 0.
SCORES: dict[str, int] = {}
# resume_id -> session dict; session_id -> suggestion buckets
SESSIONS: dict[str, dict] = {}
# resume_id -> list of chat messages; user_id -> remaining message budget
CHATS: dict[str, list] = {}
# resume_id -> list of version rows (seq is 1-based, matching the backend)
VERSIONS: dict[str, list] = {}


def apply_target_ref(resume, target_ref, text):
    """Minimal `target_ref` grammar: summary.text, exp_{i}.bullet_{j}."""
    data = json.loads(json.dumps(resume["structured_data"]))  # deep copy
    if target_ref == "summary.text":
        data.setdefault("summary", {})["text"] = text
    else:
        m = re.fullmatch(r"(exp|prj)_(\d+)\.bullet_(\d+)", target_ref)
        if m:
            key = "experience" if m.group(1) == "exp" else "projects"
            i, j = int(m.group(2)), int(m.group(3))
            rows = data.get(key) or []
            if i < len(rows) and j < len(rows[i].get("bullets") or []):
                rows[i]["bullets"][j] = text
    resume["structured_data"] = data
    resume["updated_at"] = datetime.now(timezone.utc).isoformat()


def record_version(resume_id, label):
    rows = VERSIONS.setdefault(resume_id, [])
    rows.append({"id": str(uuid.uuid4()), "resume_id": resume_id,
                 "seq": len(rows) + 1, "label": label,
                 "created_at": datetime.now(timezone.utc).isoformat()})
    r = RESUMES.get(resume_id)
    if r is not None:
        r["version_cursor"] = len(rows)
    return rows[-1]
BUDGET: dict[str, int] = {}

QUICK_ACTIONS = [
    "Make my summary stronger",
    "Improve my most recent role",
    "What is missing from this resume?",
    "Rewrite my bullets with metrics",
]
SUGGESTIONS: dict[str, dict] = {}


def make_suggestions(session_id, resume_id, gaps):
    def one(i, kw, original, suggested, placement):
        return {"id": str(uuid.uuid4()), "session_id": session_id,
                "resume_id": resume_id, "origin": "tailor",
                "section": "experience", "target_ref": f"exp_0.bullet_{i}",
                "placement": placement, "original_text": original,
                "suggested_text": suggested, "edited_text": "",
                "keywords": [kw], "reasoning":
                    f"The job description asks for {kw}; your existing work "
                    f"already demonstrates it, so the bullet now names it.",
                "status": "pending", "grounded": True,
                "critic_notes": "", "revisions": 0,
                "created_at": datetime.now(timezone.utc).isoformat()}

    active = [
        one(0, gaps[0] if gaps else "Prometheus",
            "Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
            f"Led migration of 40 services to Kubernetes with "
            f"{gaps[0] if gaps else 'Prometheus'} monitoring, cutting deploy time by 65%",
            "Acme Corp · Senior SRE"),
        one(1, "GitOps",
            "Built Terraform modules adopted by 8 engineering teams",
            "Built Terraform modules adopted by 8 engineering teams using a GitOps workflow",
            "Acme Corp · Senior SRE"),
        # A third: accept + reject + edit each consume one, and a probe that
        # runs out of suggestions silently SKIPS the edit assertion.
        one(2, gaps[1] if len(gaps) > 1 else "Grafana",
            "Cut alert noise by tuning thresholds",
            f"Cut alert noise by tuning {gaps[1] if len(gaps) > 1 else 'Grafana'} thresholds",
            "Acme Corp · Senior SRE"),
    ]
    matched = [{**one(2, "Kubernetes",
                      "Reduced pager volume by 45% by rewriting alert rules around SLOs",
                      "Reduced pager volume by 45% by rewriting alert rules around SLOs",
                      "Acme Corp · Senior SRE"),
                "status": "accepted"}]
    SUGGESTIONS[session_id] = {"active": active, "matched": matched, "rejected": []}


ANALYSES: dict[str, dict] = {}


def analysis_for(r, refresh=False):
    """Stable identity: GET must return the row POST created, not a new one."""
    if not refresh and r["id"] in ANALYSES:
        return ANALYSES[r["id"]]
    # Scored with the real algorithm so the stored report and the live steps
    # endpoint can never contradict each other — a hardcoded 72 next to a
    # computed step total is exactly the kind of mock/real divergence that
    # hides contract bugs.
    scored = score_data(r.get("structured_data") or {})
    row = {
        "id": str(uuid.uuid4()), "resume_id": r["id"],
        "overall_score": sum(sc for sc, _f in scored.values()),
        "category_scores": {
            key: {
                "score": scored[key][0],
                "max": maximum,
                "notes": [f["message"] for f in scored[key][1]],
                "findings": scored[key][1],
            }
            for key, maximum, _t, _d in STEP_META
        },
        "role_tags": ["site reliability", "mid-level · 6 yrs"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ANALYSES[r["id"]] = row
    return row


# --- date normalisation (mirrors resume_ops.normalise_entry) ----------------
# Ported rather than stubbed: the parity probe compares the mock's derived
# `dates` mirror against the real backend's, so a shortcut here would show up
# as a false pass.

_PRESENT_WORDS = {"present", "current", "currently", "now", "ongoing",
                  "to date", "date"}
_PADDED_SEP = re.compile(r"\s+(?:–|—|−|-{1,2}|to|until|through)\s+", re.IGNORECASE)
_TIGHT_SEP = re.compile(
    r"^\s*(\d{4})\s*[–—−-]\s*(\d{4}|present|current|now)\s*$", re.IGNORECASE)


def _is_present(tok):
    return tok.strip().strip(".").lower() in _PRESENT_WORDS


def split_dates(raw):
    text = (raw or "").strip()
    if not text:
        return {"start_date": "", "end_date": "", "current": False}
    if _is_present(text):
        return {"start_date": "", "end_date": "", "current": True}
    parts = _PADDED_SEP.split(text, maxsplit=1)
    if len(parts) != 2:
        t = _TIGHT_SEP.match(text)
        parts = [t.group(1), t.group(2)] if t else []
    if len(parts) != 2:
        return None
    start, end = parts[0].strip(), parts[1].strip()
    if not start:
        return None
    if _is_present(end):
        return {"start_date": start, "end_date": "", "current": True}
    if not end:
        return None
    return {"start_date": start, "end_date": end, "current": False}


def date_label(entry):
    if not isinstance(entry, dict):
        return ""
    raw = (entry.get("raw_dates") or "").strip()
    if raw:
        return raw
    start = (entry.get("start_date") or "").strip()
    end = (entry.get("end_date") or "").strip()
    if entry.get("current"):
        return ("%s – Present" % start) if start else "Present"
    if start and end:
        return "%s – %s" % (start, end)
    if start or end:
        return start or end
    return (entry.get("dates") or "").strip()


def normalise_entry(entry):
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    has_structured = any(out.get(k) for k in ("start_date", "end_date")) \
        or out.get("current") is True
    if not has_structured and not out.get("raw_dates"):
        legacy = (out.get("dates") or "").strip()
        if legacy:
            split = split_dates(legacy)
            if split is None:
                out["raw_dates"] = legacy
            else:
                out.update(split)
                out.pop("raw_dates", None)
    out.setdefault("start_date", "")
    out.setdefault("end_date", "")
    out["current"] = bool(out.get("current"))
    out["dates"] = date_label(out)
    return out


def migrate(data):
    if not isinstance(data, dict):
        return data
    out = json.loads(json.dumps(data))
    for section in ("experience", "education", "projects"):
        entries = out.get(section) or []
        if isinstance(entries, list):
            out[section] = [normalise_entry(e) for e in entries]
    return out


# --- guided step editor -------------------------------------------------
# A real port of backend/app/services/heuristics.py, not a canned payload.
# The probe asserts the +N points arithmetic here exactly as it does against
# the real backend, so a stub would defeat the whole point of the mock.

STEP_META = [
    ("contact", 15, "Contact & Profile Completeness",
     "Checks for consistency and completeness of your contact information "
     "and personal details."),
    ("summary", 20, "Professional Summary",
     "Your opening pitch — the few lines a recruiter actually reads."),
    ("experience", 45, "Work Experience Impact",
     "Whether your bullets show measurable outcomes, not duties."),
    ("format", 20, "Structure & ATS Readability",
     "Length, required sections, and whether a parser can read it."),
]

ACTION_VERBS = {
    "led", "built", "designed", "migrated", "reduced", "improved", "launched",
    "automated", "scaled", "delivered", "implemented", "architected",
    "optimized", "optimised", "created", "drove", "shipped", "owned",
    "established", "mentored",
}
METRIC_RE = re.compile(r"(\d+(\.\d+)?\s*%|\$\s?\d|\b\d{2,}\b|\bx\d+\b)")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sev(pts):
    return "high" if pts >= 5 else ("medium" if pts >= 3 else "low")


def _finding(fid, cat, ref, pts, msg, hint, action=""):
    return {"id": fid, "category": cat, "target_ref": ref, "severity": _sev(pts),
            "points": pts, "message": msg, "fix_hint": hint, "action": action,
            "meta": {}}


def _plain_text(d):
    parts = [d.get("summary", {}).get("text", "")]
    for e in d.get("experience", []) or []:
        parts += [e.get("company", ""), e.get("role", "")] + list(e.get("bullets", []))
    for e in d.get("projects", []) or []:
        parts += [e.get("name", "")] + list(e.get("bullets", []))
    for e in d.get("education", []) or []:
        parts += [e.get("school", ""), e.get("degree", "")]
    for g in d.get("skills", []) or []:
        parts += list(g.get("items", []))
    return " ".join(x for x in parts if x)


def _exp_bullets(d):
    for i, e in enumerate(d.get("experience", []) or []):
        for j, b in enumerate(e.get("bullets", []) or []):
            yield "exp_%d.bullet_%d" % (i, j), b


def score_data(d):
    """Returns (category -> (score, [findings]))."""
    out = {}

    # contact
    c = d.get("contact", {}) or {}
    pts, fs = 0, []
    for key, worth, msg, hint in [
        ("name", 4, "Missing name",
         "Your full name should be the first thing on the resume."),
        ("phone", 3, "Missing phone number",
         "Recruiters often call before they email. Add a reachable number."),
        ("location", 2, "Missing location",
         "Add at least city and country — many ATS filters sort on location."),
    ]:
        if c.get(key):
            pts += worth
        else:
            fs.append(_finding("contact.%s.missing" % key, "contact",
                               "contact.%s" % key, worth, msg, hint))
    em = c.get("email", "") or ""
    if em and EMAIL_RE.match(em):
        pts += 4
    else:
        fs.append(_finding("contact.email.invalid", "contact", "contact.email", 4,
                           "Missing or malformed email" if em else "Missing email",
                           "Use a professional address you actually check."))
    if c.get("links"):
        pts += 2
    else:
        fs.append(_finding("contact.links.missing", "contact", "contact.links", 2,
                           "No profile links",
                           "Add LinkedIn, GitHub or a portfolio — it is free credibility."))
    out["contact"] = (min(pts, 15), fs)

    # summary
    t = (d.get("summary", {}) or {}).get("text", "").strip()
    if not t:
        out["summary"] = (0, [_finding("summary.missing", "summary", "summary.text",
                                       20, "No professional summary",
                                       "Three lines: your role, your years, and "
                                       "your strongest result.", "rewrite")])
    else:
        fs = []
        w = len(t.split())
        length = 8 if w >= 25 else (5 if w >= 12 else 2)
        pts = length
        if length < 8:
            fs.append(_finding("summary.too_short", "summary", "summary.text",
                               8 - length,
                               "Summary is short (%d words) — aim for 30–60" % w,
                               "Name the role you want and quantify one achievement.",
                               "rewrite"))
        if METRIC_RE.search(t):
            pts += 6
        else:
            fs.append(_finding("summary.no_metric", "summary", "summary.text", 6,
                               "Summary has no quantified achievement",
                               "Add a real number — team size, users served, latency cut.",
                               "rewrite"))
        if any(v in t.lower() for v in ACTION_VERBS):
            pts += 6
        else:
            fs.append(_finding("summary.no_action_verb", "summary", "summary.text", 6,
                               "Summary lacks a strong action verb",
                               "Open with a verb like 'Led', 'Built' or 'Scaled'.",
                               "rewrite"))
        out["summary"] = (min(pts, 20), fs)

    # experience
    entries = d.get("experience", []) or []
    bullets = [b for e in entries for b in e.get("bullets", [])]
    if not entries:
        out["experience"] = (0, [_finding("experience.missing", "experience",
                                          "experience", 45,
                                          "No work experience listed",
                                          "Add your roles, most recent first.")])
    elif not bullets:
        out["experience"] = (6, [_finding("experience.no_bullets", "experience",
                                          "experience", 39,
                                          "Experience entries have no bullet points",
                                          "Describe what you did and what changed "
                                          "because of it.", "rewrite")])
    else:
        fs, pts = [], 10
        quant = sum(1 for b in bullets if METRIC_RE.search(b))
        award = int(round((quant / len(bullets)) * 18))
        pts += award
        if award < 18:
            ref = next((r for r, b in _exp_bullets(d) if not METRIC_RE.search(b)),
                       "experience")
            fs.append(_finding("experience.unquantified_bullets", "experience", ref,
                               18 - award,
                               "Only %d of %d bullets are quantified" % (quant, len(bullets)),
                               "Add the number you moved: %, time saved, revenue, scale.",
                               "rewrite"))
        strong = sum(1 for b in bullets
                     if b.strip().split()[:1]
                     and b.strip().split()[0].lower() in ACTION_VERBS)
        vaward = int(round((strong / len(bullets)) * 12))
        pts += vaward
        if vaward < 12:
            ref = next((r for r, b in _exp_bullets(d)
                        if not (b.strip().split()[:1]
                                and b.strip().split()[0].lower() in ACTION_VERBS)),
                       "experience")
            fs.append(_finding("experience.weak_verbs", "experience", ref,
                               12 - vaward,
                               "%d of %d bullets do not start with an action verb"
                               % (len(bullets) - strong, len(bullets)),
                               "Start with what you DID: Led, Built, Reduced, Migrated.",
                               "rewrite"))
        missing = [i for i, e in enumerate(entries) if not date_label(e)]
        if not missing:
            pts += 5
        else:
            fs.append(_finding("experience.missing_dates", "experience",
                               "exp_%d" % missing[0], 5,
                               "%d role(s) missing dates" % len(missing),
                               "Employment gaps are judged less harshly than "
                               "missing dates."))
        out["experience"] = (min(pts, 45), fs)

    # format
    fs, pts = [], 0
    if d.get("skills"):
        pts += 5
    else:
        fs.append(_finding("format.no_skills", "format", "skills", 5,
                           "No skills section",
                           "ATS keyword matching leans heavily on this section."))
    if d.get("education"):
        pts += 4
    else:
        fs.append(_finding("format.no_education", "format", "education", 4,
                           "No education section",
                           "Add your highest qualification, even if it is older."))
    w = len(_plain_text(d).split())
    if 250 <= w <= 900:
        pts += 6
    elif w < 250:
        pts += 2
        fs.append(_finding("format.too_thin", "format", "experience", 4,
                           "Resume looks thin — %d words" % w,
                           "Aim for 250–900 words. Add detail to your recent roles."))
    else:
        pts += 3
        fs.append(_finding("format.too_long", "format", "experience", 3,
                           "Resume may exceed two pages — %d words" % w,
                           "Cut older roles back to two or three bullets."))
    if bullets:
        over = [r for r, b in _exp_bullets(d) if len(b.split()) > 45]
        if over:
            pts += 2
            fs.append(_finding("format.overlong_bullets", "format", over[0], 3,
                               "%d bullet(s) are very long" % len(over),
                               "Keep bullets under about 30 words so they get read.",
                               "rewrite"))
        else:
            pts += 5
    out["format"] = (min(pts, 20), fs)
    return out


def steps_for(r):
    d = r.get("structured_data") or {}
    scored = score_data(d)
    steps = []
    for index, (key, maximum, title, desc) in enumerate(STEP_META):
        score, fs = scored[key]
        avail = sum(f["points"] for f in fs)
        steps.append({
            "id": key, "index": index, "title": title, "description": desc,
            "score": score, "max": maximum, "points_available": avail,
            "finding_count": len(fs),
            "status": "clear" if not fs else ("attention" if avail >= 5 else "minor"),
            "findings": fs,
        })
    return {
        "resume_id": r["id"],
        "overall_score": sum(s["score"] for s in steps),
        "max_score": sum(s["max"] for s in steps),
        "points_available": sum(s["points_available"] for s in steps),
        "steps": steps,
    }


def summary(r):
    row = {k: r[k] for k in
           ("id", "title", "template_key", "parse_status", "kind", "parent_id",
            "tailored_for", "version_cursor", "updated_at")}
    row["overall_score"] = SCORES.get(r["id"])
    row["structured_data"] = r["structured_data"]
    return row


def new_record(uid, title, **over):
    rid = str(uuid.uuid4())
    rec = {"id": rid, "user_id": uid, "title": title,
           "structured_data": DEMO, "storage_key": f"{uid}/{rid}/resume.txt",
           "parse_status": "ready", "parse_note": "", "template_key": "modern",
           "kind": "base", "parent_id": None, "tailored_for": "",
           "version_cursor": 0,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    rec.update(over)
    RESUMES[rid] = rec
    return rec


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _uid(self):
        return self.headers.get("X-User-Id")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/health":
            return self._send(200, {
                "status": "ok", "database": "sqlite (mock)", "pgvector": False,
                "storage": "disk", "llm_configured": False,
                "model": "mock", "users": len(USERS)})
        if p == "/api/admin/agent-runs":
            return self._send(200, [])
        if p == "/api/users":
            return self._send(200, USERS)
        if p == "/api/templates":
            return self._send(200, TEMPLATES)
        if p == "/api/resumes":
            uid = self._uid()
            return self._send(200, [summary(r) for r in RESUMES.values()
                                    if r["user_id"] == uid])
        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            return self._send(200, r)
        if p == "/api/sample-jd":
            return self._send(200, {
                "id": str(uuid.uuid4()), "title": "Site Reliability Engineer",
                "content": "We are hiring an SRE. Required: Kubernetes, Terraform, "
                           "Prometheus, Grafana, AWS, GitOps, Python or Go. You will "
                           "own production reliability, define SLOs and reduce toil "
                           "through automation.",
                "extracted_keywords": ["Kubernetes", "Terraform", "Prometheus"],
                "is_sample": True,
                "created_at": datetime.now(timezone.utc).isoformat()})

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/versions", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            rows = VERSIONS.get(r["id"]) or [record_version(r["id"], "Seeded")]
            return self._send(200, list(reversed(rows)))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/export", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fmt = (q.get("format") or ["pdf"])[0]
            if fmt not in {"pdf", "docx", "txt"}:
                return self._send(422, {"detail":
                    f"{fmt}: expected one of ['docx', 'pdf', 'txt']"})
            # Real magic bytes: the UI sniffs these when saving a blob.
            blobs = {"pdf": b"%PDF-1.4\n% mock\n", "docx": b"PK\x03\x04mock",
                     "txt": b"Mock export\n"}
            types = {"pdf": "application/pdf", "txt": "text/plain",
                     "docx": "application/vnd.openxmlformats-officedocument."
                             "wordprocessingml.document"}
            body = blobs[fmt]
            self.send_response(200)
            self.send_header("Content-Type", types[fmt])
            self.send_header("Content-Disposition",
                             f'attachment; filename="resume.{fmt}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return self.wfile.write(body)

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/chat", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            return self._send(200, {
                "messages": CHATS.get(r["id"], []),
                "quick_actions": QUICK_ACTIONS,
                "tokens_left": BUDGET.get(self._uid(), 25)})

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/analysis/steps", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            # Never 404s on "not analysed": the guided flow creates the score.
            return self._send(200, steps_for(r))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/analysis", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            if r["id"] not in SCORES:
                return self._send(404, {"detail": "No analysis for this resume"})
            return self._send(200, analysis_for(r))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/tailor", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            return self._send(200, list(reversed(SESSIONS.get(r["id"], []))))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/tailor/([0-9a-f-]+)/suggestions", p)
        if m:
            return self._send(200, SUGGESTIONS.get(m.group(2),
                                                   {"active": [], "matched": [], "rejected": []}))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/parse-status", p)
        if m:
            r = RESUMES.get(m.group(1))
            if r and r["user_id"] != self._uid():
                r = None  # cross-user reads are 404, never a leak
            if not r:
                return self._send(404, {"detail": "Resume not found"})
            return self._send(200, {"resume_id": r["id"],
                                    "parse_status": r["parse_status"],
                                    "parse_note": r["parse_note"]})
        return self._send(404, {"detail": "Not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""

        if p == "/api/resumes/upload":
            uid = self._uid()
            if not uid:
                return self._send(422, {"detail": "X-User-Id header required"})
            # crude multipart scrape: filename + title field
            text = raw.decode("utf-8", "replace")
            fn = re.search(r'filename="([^"]+)"', text)
            filename = fn.group(1) if fn else "resume.txt"
            tm = re.search(r'name="title"\r\n\r\n(.*?)\r\n', text, re.S)
            title = (tm.group(1).strip() if tm else "") or filename
            suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if suffix not in {".pdf", ".docx", ".txt"}:
                # 422, matching the real backend: UnsupportedFormat subclasses
                # ValidationError, which main.py maps to 422.
                return self._send(422, {"detail":
                    f"{suffix or filename}: expected one of ['.docx', '.pdf', '.txt']"})
            rec = new_record(uid, title)  # DEMO stands in for the real parser
            return self._send(201, rec)

        if p == "/api/resumes":
            uid = self._uid()
            title = json.loads(raw or b"{}").get("title") or "Untitled resume"
            return self._send(201, new_record(uid, title))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/fork", p)
        if m:
            src = RESUMES.get(m.group(1))
            if not src or src["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            body = json.loads(raw or b"{}")
            child = new_record(
                src["user_id"],
                body.get("title") or f"{src['title']} (copy)",
                structured_data=json.loads(json.dumps(src["structured_data"])),
                template_key=src["template_key"],
                kind="tailored",
                parent_id=src["id"],
                tailored_for=body.get("tailored_for", ""),
            )
            return self._send(201, child)

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/chat", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            uid = self._uid()
            left = BUDGET.get(uid, 25)
            if left <= 0:
                return self._send(429, {"detail": "Message budget exhausted"})
            body = json.loads(raw or b"{}")
            now = datetime.now(timezone.utc).isoformat()
            hist = CHATS.setdefault(r["id"], [])
            hist.append({"id": str(uuid.uuid4()), "resume_id": r["id"],
                         "role": "user", "content": body.get("content", ""),
                         "suggestion_id": None, "created_at": now})

            # Every 2nd reply carries a suggestion so the inline
            # Accept/Reject card is actually exercised, not assumed.
            turn = len([m for m in hist if m["role"] == "user"])
            sug = None
            sug_id = None
            if turn % 2 == 0:
                sug_id = str(uuid.uuid4())
                sug = {"id": sug_id, "session_id": None, "resume_id": r["id"],
                       "origin": "chat", "section": "summary",
                       "target_ref": "summary.text", "placement": "Summary",
                       "original_text": "Experienced engineer.",
                       "suggested_text": "SRE with 6 years keeping high-traffic "
                                         "platforms available and on budget.",
                       "edited_text": "", "keywords": ["SRE"],
                       "reasoning": "The original is generic and omits your level.",
                       "status": "pending", "grounded": True,
                       "critic_notes": "", "revisions": 0, "created_at": now}
                SUGGESTIONS.setdefault("chat", {"active": [], "matched": [],
                                                "rejected": []})["active"].append(sug)

            reply = {"id": str(uuid.uuid4()), "resume_id": r["id"],
                     "role": "assistant",
                     "content": "Here is what stands out. Your experience "
                                "section is strong, but the summary does not "
                                "state your level or specialism."
                                + (" I have drafted a replacement below."
                                   if sug else ""),
                     "suggestion_id": sug_id, "created_at": now}
            hist.append(reply)
            BUDGET[uid] = left - 1
            return self._send(201, {"message": reply, "suggestion": sug,
                                    "tokens_left": BUDGET[uid]})

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/(undo|redo)", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            rows = VERSIONS.setdefault(r["id"], [])
            if not rows:
                record_version(r["id"], "Seeded")
                rows = VERSIONS[r["id"]]
            cur = r.get("version_cursor", len(rows))
            r["version_cursor"] = (max(0, cur - 1) if m.group(2) == "undo"
                                   else min(len(rows), cur + 1))
            return self._send(200, r)

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/analyze", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            row = analysis_for(r, refresh=True)
            SCORES[r["id"]] = row["overall_score"]
            return self._send(201, row)

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/tailor", p)
        if m:
            src = RESUMES.get(m.group(1))
            if not src or src["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            body = json.loads(raw or b"{}")
            label = (body.get("jd_title") or "Untitled role").strip()
            target = src
            if body.get("fork", True):
                target = new_record(
                    src["user_id"], f"{src['title']} - {label}",
                    structured_data=json.loads(json.dumps(src["structured_data"])),
                    template_key=src["template_key"],
                    kind="tailored", parent_id=src["id"], tailored_for=label)
            words = [w.strip(",.") for w in (body.get("jd_content") or "").split()]
            stop = {"need", "we", "you", "the", "our", "required", "must",
                    "responsibilities", "requirements", "about", "join", "this"}
            gaps = [w for w in words
                    if w and w[0].isupper() and w.lower() not in stop][:4] or ["Prometheus"]
            sid = str(uuid.uuid4())
            session = {
                "id": sid, "resume_id": target["id"],
                "job_description_id": str(uuid.uuid4()),
                "match_percent": 61.5, "baseline_percent": 42.0,
                "matched_keywords": ["Kubernetes", "Terraform"],
                "gap_keywords": gaps,
                "thread_id": "mock", "graph_state": "ready",
                "created_at": datetime.now(timezone.utc).isoformat()}
            SESSIONS.setdefault(target["id"], []).append(session)
            make_suggestions(sid, target["id"], gaps)
            return self._send(201, session)

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/template", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            key = json.loads(raw or b"{}").get("template_key", "")
            if key not in {t["key"] for t in TEMPLATES}:
                return self._send(404, {"detail": f"Unknown template: {key}"})
            r["template_key"] = key
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            return self._send(200, r)

        return self._send(404, {"detail": "Not found"})

    def do_PUT(self):
        p = self.path.split("?")[0]
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)/data", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            body = json.loads(raw or b"{}")
            if "structured_data" not in body:
                return self._send(422, {"detail": "structured_data required"})
            # Normalise on write, exactly as resume_service.update_data does.
            r["structured_data"] = migrate(body["structured_data"])
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            record_version(r["id"], "Manual edit")
            return self._send(200, r)

        return self._send(404, {"detail": "Not found"})

    def do_PATCH(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        m = re.fullmatch(r"/api/suggestions/([0-9a-f-]+)", p)
        if m:
            body = json.loads(raw or b"{}")
            action = body.get("action")
            if action not in {"accept", "reject", "edit"}:
                return self._send(422, {"detail":
                    "action must be accept, reject or edit"})
            for sid, buckets in SUGGESTIONS.items():
                for sug in list(buckets["active"]):
                    if sug["id"] != m.group(1):
                        continue
                    buckets["active"].remove(sug)
                    if action == "reject":
                        sug["status"] = "rejected"
                        buckets["rejected"].append(sug)
                    else:
                        sug["status"] = "accepted"
                        # Really patch the resume: a mock that only flips a
                        # status lets "accept does nothing" ship unnoticed.
                        tgt = RESUMES.get(sug["resume_id"])
                        text = sug.get("edited_text") or sug["suggested_text"]
                        if tgt is not None:
                            apply_target_ref(tgt, sug["target_ref"], text)
                        record_version(sug["resume_id"], "Accepted suggestion")
                        if action == "edit":
                            sug["edited_text"] = body.get("edited_text", "")
                        buckets["matched"].append(sug)
                        for sess in SESSIONS.get(sug["resume_id"], []):
                            if sess["id"] == sid:
                                sess["match_percent"] = min(
                                    100.0, sess["match_percent"] + 12.5)
                    return self._send(200, sug)
            return self._send(404, {"detail": "Suggestion not found"})

        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            title = (json.loads(raw or b"{}").get("title") or "").strip()
            if not title:
                return self._send(422, {"detail": "Title cannot be empty"})
            r["title"] = title
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            return self._send(200, r)
        return self._send(404, {"detail": "Not found"})

    def do_DELETE(self):
        p = self.path.split("?")[0]
        m = re.fullmatch(r"/api/resumes/([0-9a-f-]+)", p)
        if m:
            r = RESUMES.get(m.group(1))
            if not r or r["user_id"] != self._uid():
                return self._send(404, {"detail": "Resume not found"})
            # Forks are ORPHANED, never cascaded.
            orphaned = 0
            for other in RESUMES.values():
                if other.get("parent_id") == r["id"]:
                    other["parent_id"] = None
                    orphaned += 1
            del RESUMES[r["id"]]
            SCORES.pop(r["id"], None)
            return self._send(200, {"deleted": r["id"], "vectors_removed": 0,
                                    "objects_removed": 0,
                                    "children_orphaned": orphaned})
        return self._send(404, {"detail": "Not found"})


if __name__ == "__main__":
    print("mock API on http://0.0.0.0:8000  (users seeded:",
          ", ".join(u["name"] for u in USERS) + ")")
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
