/**
 * Response and request types, mirrored from backend/app/schemas.py.
 *
 * STOPGAP: the real artifact is `npm run gen:types` against the live OpenAPI
 * schema. That needs a running backend, so these were transcribed from
 * schemas.py by hand and are accurate as of today. Regenerate after any
 * backend change rather than editing these by hand.
 */

/* ---------- users ---------- */

export interface UserOut {
  id: string;
  email: string;
  name: string;
  title: string;
  avatar_color: string;
  chat_tokens_left: number;
}

/* ---------- templates ---------- */

/** Body typeface family. */
export type TemplateFont = "sans" | "serif";
/** How a section heading is separated from its content. */
export type TemplateHeading = "rule" | "underline" | "plain" | "boxed" | "sidebar";
/** Vertical rhythm. */
export type TemplateDensity = "airy" | "normal" | "dense";
/** Header composition. */
export type TemplateHeader = "stacked" | "split";
/** How one experience/project entry is laid out. */
export type TemplateEntry = "stacked" | "inline";

/**
 * The styling contract, defined once in backend seed.py and read by BOTH the
 * live HTML preview and the PDF exporter. Adding a template is a backend-only
 * change — no frontend edit required.
 */
export interface DesignTokens {
  font: TemplateFont;
  heading: TemplateHeading;
  name_align: "left" | "center";
  /** Hex, or "" to inherit the neutral ink colour. */
  accent: string;
  density: TemplateDensity;
  /** UPPERCASE section headings. */
  caps: boolean;
  /** Rule under the contact line. */
  divider: boolean;
  /** "stacked" = name above contact; "split" = name left, contact right. */
  header: TemplateHeader;
  /** "stacked" = role above company; "inline" = "Role - Company" on one line. */
  entry: TemplateEntry;
  /** Which line carries the dates: the role, or the employer. */
  entry_lead: "role" | "company";
  /** "sidebar" moves skills/education/extras into a right rail. */
  layout: "single" | "sidebar";
  /** Separator between role and company on an inline entry row. */
  entry_sep: string;
  /** "inline" folds school into the degree row; location moves beside dates. */
  edu: "stacked" | "inline";
  /** Reference templates set the floated-right dates in italic. */
  dates_style: "plain" | "italic";
  /** Outline icons beside section headings + contact items (vector in the
   *  PDF, inline SVG here -- never text, so re-import never sees them). */
  icons: boolean;
  /** Stacked education order: headline the degree, or the school (executive
   *  reference / fix-list defect 9). The inline fold does not reverse. */
  edu_lead: "degree" | "school";
}

export interface TemplateOut {
  id: string;
  key: string;
  name: string;
  description: string;
  design_tokens: DesignTokens;
}

/* ---------- resumes ---------- */

export type ParseStatus = "pending" | "ready" | "failed";

export interface Contact {
  name: string;
  /** Professional title under the name. Optional: older resumes lack it. */
  headline?: string;
  email: string;
  phone: string;
  location: string;
  links: string[];
}

/**
 * Experience dates are structured. `dates` is a DERIVED mirror maintained by
 * the backend (resume_ops.normalise_entry) — read it for display if you like,
 * but never write it: the editor writes start_date/end_date/current and the
 * server recomputes the label. Use `dateLabel()` to render.
 *
 * `raw_dates` is set only when a legacy free-text range could not be split
 * confidently. It is shown verbatim rather than guessed at.
 */
export interface ExperienceEntry {
  company: string;
  role: string;
  location?: string;
  start_date?: string;
  end_date?: string;
  current?: boolean;
  raw_dates?: string;
  /** Derived. Do not write. */
  dates: string;
  bullets: string[];
  /** Italic one-liner under the entry header: context for an unfamiliar
   *  employer ("Bootstrapped SaaS unicorn"). Data-only -- the parser never
   *  recovers it from a PDF, so a re-imported resume loses it. */
  descriptor?: string;
  /** One bold line with dates, no bullets -- for older roles that no longer
   *  carry the story. Data-only for the same reason. */
  condensed?: boolean;
}

/** Projects use `name` where experience uses company/role — parsing.py
 *  renames the key when it builds the projects list. */
export interface ProjectEntry {
  name: string;
  role?: string;
  url?: string;
  tech?: string[];
  start_date?: string;
  end_date?: string;
  current?: boolean;
  raw_dates?: string;
  /** Derived. Do not write. */
  dates?: string;
  bullets: string[];
}

export interface EducationEntry {
  school: string;
  degree: string;
  field_of_study?: string;
  location?: string;
  grade?: string;
  start_date?: string;
  end_date?: string;
  current?: boolean;
  raw_dates?: string;
  /** Derived. Do not write. */
  dates: string;
}

export interface SkillGroup {
  label: string;
  items: string[];
}

/** The six optional-section presets. `custom` is a user-titled section. */
export type ExtraKind =
  | "certifications" | "languages" | "awards"
  | "publications" | "references" | "custom";

/**
 * One entry in an optional section. Four fields serve all six kinds; the
 * PRESET supplies the labels, and a field whose label is "" is not part of
 * that kind and is never rendered.
 */
export interface ExtraEntry {
  primary: string;
  secondary: string;
  date: string;
  detail: string;
}

export interface ExtraSection {
  kind: ExtraKind;
  title: string;
  entries: ExtraEntry[];
}

export interface StructuredData {
  contact: Contact;
  summary: { text: string };
  experience: ExperienceEntry[];
  projects: ProjectEntry[];
  education: EducationEntry[];
  skills: SkillGroup[];
  extras: ExtraSection[];
}

export type ResumeKind = "base" | "tailored";

export interface ResumeSummaryOut {
  id: string;
  title: string;
  template_key: string;
  parse_status: ParseStatus;
  kind: ResumeKind;
  /** Base resume this was forked from; null once that base is deleted. */
  parent_id: string | null;
  /** JD label for the card subtitle, e.g. "SRE at Cognizant". */
  tailored_for: string;
  version_cursor: number;
  updated_at: string;
  /** Latest analysis score. NULL means never analysed -- not zero. */
  overall_score: number | null;
  /** Enough content to render the card thumbnail without a fetch per card. */
  structured_data: StructuredData;
}

export interface ResumeOut {
  id: string;
  user_id: string;
  title: string;
  structured_data: StructuredData;
  kind: ResumeKind;
  parent_id: string | null;
  tailored_for: string;
  storage_key: string;
  parse_status: ParseStatus;
  parse_note: string;
  template_key: string;
  version_cursor: number;
  updated_at: string;
}

export interface ParseStatusOut {
  resume_id: string;
  parse_status: ParseStatus;
  parse_note: string;
  /**
   * Advisories about the uploaded FILE's machine-readability.
   *
   * Distinct from `parse_note`, which reports that reading failed. These
   * accompany a SUCCESSFUL parse and say the reading may have been shaky —
   * two columns, a scan, an unrecognised heading. None is a scored
   * finding; the score is unaffected.
   */
  parse_advisories?: string[];
}

export interface DeleteOut {
  deleted: string;
  vectors_removed: number;
  objects_removed: number;
  /**
   * Forks of a deleted base are ORPHANED, not cascaded — deleting your base
   * must never silently delete tailored versions already sent to employers.
   */
  children_orphaned: number;
}

/* ---------- analysis ---------- */

/** One of contact | summary | experience | format. Weights in heuristics.py
 *  are 15 / 20 / 45 / 20.
 *
 *  `notes` are plain strings kept for backward compatibility; they are derived
 *  from `findings[].message`. New UI should read `findings`, which carry a
 *  target_ref and a points value. */
export interface CategoryScore {
  score: number;
  max: number;
  /**
   * True for the two 0-point gates (contact = knockout filter, format =
   * pass/fail parser blocker, 2026-10-25 ruling): findings still surface,
   * but no points hang off them, and the category cannot prop up the score.
   */
  advisory?: boolean;
  notes: string[];
  findings?: Finding[];
}

/**
 * One actionable recommendation from the rule engine.
 *
 * `points` is a promise the backend tests enforce: fixing this raises
 * overall_score by exactly this much. Never render an adjusted or estimated
 * number here — if it stops matching, the whole guided flow loses credibility.
 *
 * `target_ref` uses the resume_ops grammar (`summary.text`,
 * `exp_{i}.bullet_{j}`, `skills.{i}.items`, …) or names a bare section.
 */
export interface Finding {
  id: string;
  category: CategoryKey;
  target_ref: string;
  severity: "high" | "medium" | "low";
  points: number;
  message: string;
  fix_hint: string;
  /** "" | "rewrite" | "rerank" | "quantify" — which one-click action
   *  applies, if any.
   *
   *  `quantify` is NOT a rewrite. The two metric findings are cleared only
   *  by a number the writer must never invent, so that path restructures
   *  the line so a figure fits and leaves a literal `[__]` for the user.
   *  Labelling it "Rewrite with AI" would promise the figure. */
  action: string;
  meta: Record<string, unknown>;
}

/** A single screen of the guided editor. */
export interface AnalysisStep {
  id: StepKey;
  index: number;
  title: string;
  description: string;
  score: number;
  max: number;
  points_available: number;
  finding_count: number;
  /** "optional" marks a navigable step that carries no points (extras). */
  status: "clear" | "minor" | "attention" | "optional";
  /**
   * True for the 0-point gates (contact = knockout filter, format = pass/fail
   * parser blocker): findings emit, nothing is added to the score.
   */
  advisory?: boolean;
  findings: Finding[];
}

/**
 * GET /resumes/{id}/analysis/steps — recomputed live from structured_data on
 * every call, so it reflects edits immediately and never 404s.
 */
export interface AnalysisStepsOut {
  resume_id: string;
  overall_score: number;
  max_score: number;
  points_available: number;
  steps: AnalysisStep[];
  /**
   * ScoringAgent prose, carried alongside the live steps.
   *
   * Generated and persisted on every analysis since the beginning, and read
   * by no screen until now -- a model call per analysis whose output was
   * thrown away.
   */
  commentary: Commentary;
  /**
   * True when the resume changed after that prose was written.
   *
   * The steps beside it are recomputed live, so the two CAN disagree, and
   * the prose is the half that looks authoritative. Never present stale
   * commentary as current -- stamp it.
   */
  commentary_stale: boolean;
}

/**
 * Scoring categories. Every Finding belongs to exactly one.
 *
 * 2026-10-25 ATS table: summary+education 10, skills 30, experience 45,
 * projects 15. `contact` and `format` remain categories, but advisory-only:
 * they are the knockout filter and the pass/fail parser blocker, worth 0
 * points, emitting findings without ever moving the score. Projects moved
 * from navigable-unscored to a scored category under the same ruling.
 */
export type CategoryKey =
  | "contact"
  | "summary"
  | "skills"
  | "experience"
  | "projects"
  | "format";

/**
 * Step ids. A superset of CategoryKey: "extras" is navigable but unscored
 * and NEVER emits findings, which is why Finding.category stays CategoryKey.
 */
export type StepKey = CategoryKey | "extras";

export interface AtsExpansion {
  acronym: string;
  /** Always carries both forms: "Project Management Professional (PMP)". */
  expanded: string;
}

/**
 * Advisory machine-readability notes from AtsAgent.
 *
 * Never affects overall_score — the number stays deterministic. All three
 * fields are always present and may all be empty: the agent is gated (a tidy
 * resume asks nothing of a model) and goes silent without a provider key.
 * Treat "empty" as normal, not as an error.
 */
export interface AtsReview {
  expansions: AtsExpansion[];
  /**
   * Tier 2: is this number plausible for the work described? Judgement the
   * deterministic rules cannot make ("cut p95 by 100%" IS judged by a rule;
   * "10 jobs/hour on a microservices pipeline" needs the domain knowledge
   * only a model has). Advisory only — no points, ever.
   */
  plausibility: AtsMetricAdvisory[];
  note: string;
}

/** One plausibility advisory. `quote` is verbatim text from the bullet. */
export interface AtsMetricAdvisory {
  bullet: string;
  quote: string;
  advice: string;
}

/** Prose from ScoringAgent. The number is never the LLM's to decide. */
export interface Commentary {
  headline: string;
  category_notes: Record<string, string>;
  /**
   * Per category, what the resume already does RIGHT.
   *
   * Rides on the same model call as `category_notes`, so it costs no extra
   * tokens and cannot contradict the prose beside it. Optional because
   * reports written before this field existed simply lack the key, and
   * because the rule-engine fallback returns `{}` -- with no model there is
   * nothing to praise with, and "this check passed" is not a strength.
   *
   * Already filtered server-side: a category scoring under half marks
   * returns nothing, claims carrying unsourced numbers are dropped, and the
   * list is capped at 3. Render what arrives; do not pad it.
   */
  strengths?: Record<string, string[]>;
}

export interface AnalysisOut {
  id: string;
  resume_id: string;
  /**
   * Recomputed from the CURRENT resume on every read, not replayed from the
   * stored report — so this never disagrees with the guided editor's number.
   * `created_at` dates the prose below it, not these two fields.
   */
  overall_score: number;
  category_scores: Record<CategoryKey, CategoryScore>;
  role_tags: string[];
  commentary: Commentary;
  ats_review: AtsReview;
  created_at: string;
  /** The resume changed after the prose was written. Same flag the guided
   *  editor stamps, so the two screens cannot disagree about its age. */
  commentary_stale: boolean;
}

/* ---------- tailoring ---------- */

export interface TailorIn {
  jd_title?: string;
  jd_content: string;
  /**
   * Tailoring edits the resume IN PLACE by default, so the Analysis and
   * Tailor tabs always describe the same document.
   *
   * Set true to copy the resume first and keep a pristine base; then the
   * session's `resume_id` is the CHILD and the caller must navigate to it.
   * Either way, read the id off the session rather than assuming.
   */
  fork?: boolean;
}

export interface TailorSessionOut {
  id: string;
  resume_id: string;
  job_description_id: string;
  match_percent: number;
  baseline_percent: number;
  matched_keywords: string[];
  gap_keywords: string[];
  thread_id: string;
  graph_state: string;
  created_at: string;
}

/* ---------- suggestions ---------- */

export type SuggestionAction = "accept" | "reject" | "edit";
/**
 * Row status as stored by the backend.
 *
 * NOT the same vocabulary as the bucket names: `list_for_session` groups
 * pending -> "active" and accepted -> "matched". Conflating the two makes a
 * status check silently never match.
 */
/**
 * `needs_input` is a QUESTION, not a draft: `suggested_text` is empty and
 * there is nothing to accept until it has been answered. It is deliberately
 * not terminal -- the user can still act on it.
 */
export type SuggestionStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "needs_input";

export interface SuggestionOut {
  id: string;
  session_id: string | null;
  resume_id: string;
  origin: string;
  section: string;
  /** e.g. "summary.text", "exp_0.bullet_2", "prj_1.bullet_0", "skills.3.items" */
  target_ref: string;
  placement: string;
  original_text: string;
  suggested_text: string;
  edited_text: string;
  keywords: string[];
  reasoning: string;
  status: SuggestionStatus;
  grounded: boolean;
  critic_notes: string;
  revisions: number;

  /* ---------- clarifying question (PLAN-8 Inc 6) ---------- */
  /** Non-empty only while `status === "needs_input"`. */
  question: string;
  /** What the user typed, verbatim. The audit record of the attestation. */
  answer: string;
  /**
   * This rests on a fact the USER supplied, not on resume text.
   *
   * Stays true after answering, which is how a card whose status has moved
   * on to `pending` keeps showing "you told us this". Deliberately separate
   * from `grounded` (= supported by existing resume text), which is set
   * false on these: collapsing the two would launder an attestation into a
   * verified fact.
   */
  attested: boolean;
  /**
   * A bare "Yes" is not enough. True when the job description attached a
   * duration to the keyword -- pressing a button cannot attest "3+ years".
   */
  requires_text: boolean;

  /**
   * What accepting this does to the RESUME score -- the same currency the
   * Analysis tab prints, so the two tabs are comparable.
   *
   * Usually 0: most keyword swaps are score-neutral, and saying so is more
   * honest than hiding it. Should never be negative -- the review gate
   * rejects drafts that introduce a finding -- so a negative value means
   * that gate has a hole and the UI will show it rather than hide it.
   */
  score_delta: number;
  /**
   * The resume text this was written against has since changed.
   *
   * Accepting it would overwrite newer work, so the backend refuses with a
   * 409. That refusal used to be the FIRST the user heard of it, after
   * reading the card and clicking Accept. Now the card knows up front.
   */
  is_stale: boolean;
}

export interface SuggestionBuckets {
  active: SuggestionOut[];
  matched: SuggestionOut[];
  rejected: SuggestionOut[];
}

/* ---------- chat ---------- */

/**
 * Structured payload on a chat row.
 *
 * Present on `event` rows (edits mirrored from the Analyze/Tailor panes) and
 * on the synthetic `user` turn a "Fix with AI" click writes. Empty `{}` for
 * ordinary typed conversation.
 */
export interface ChatMessageMeta {
  kind?:
    | "fix_requested"
    | "suggestion_drafted"
    | "suggestion_accepted"
    /** The AI agent could not be reached; this reply came from the rules. */
    | "agent_unreachable";
  retryable?: boolean;
  /** Scrubbed provider failure, e.g. "LLMUnavailable: All models failed...". */
  reason?: string;
  target_ref?: string;
  origin?: string;
  applied?: boolean;
  placement?: string;
  /** Snapshot of the text as applied -- not a live read of the suggestion. */
  applied_text?: string;
  draft_text?: string;
  score_before?: number;
  score_after?: number;
  score_delta?: number;
}

export interface ChatMessageOut {
  id: string;
  resume_id: string;
  /** "user" | "assistant" | "event". */
  role: string;
  content: string;
  suggestion_id: string | null;
  meta?: ChatMessageMeta;
  created_at: string;
}

export interface ChatHistoryOut {
  messages: ChatMessageOut[];
  /** Backend-owned copy. Render verbatim as chips; do not hardcode. */
  quick_actions: string[];
  tokens_left: number;
}

export interface ChatReplyOut {
  message: ChatMessageOut;
  suggestion: SuggestionOut | null;
  tokens_left: number;
}

/** One suggestion the bulk accept declined to apply, and why. */
export interface SkippedSuggestion {
  id: string;
  /** "stale" | "needs_input" | "empty" | "missing_target" | "over_limit" */
  reason: string;
}

export interface AcceptAllOut {
  applied: string[];
  applied_count: number;
  skipped: SkippedSuggestion[];
  skipped_count: number;
  /** The cursor BEFORE the batch — the seq "Decline all" restores to. */
  undo_to_version: number;
  version_cursor: number;
}

/* ---------- versions ---------- */

export interface VersionOut {
  id: string;
  resume_id: string;
  seq: number;
  change_source: string;
  label: string;
  created_at: string;
}

/* ---------- exports ---------- */

export type ExportFormat = "pdf" | "docx" | "txt";

/* ---------- misc ---------- */

export interface JobDescriptionOut {
  id: string;
  title: string;
  content: string;
  extracted_keywords: string[];
}

export interface AgentRunOut {
  id: string;
  thread_id: string;
  agent: string;
  task: string;
  model: string;
  status: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  created_at: string;
}

export interface HealthOut {
  status: string;
  database: string;
  pgvector: boolean;
  /** "minio" | "disk" — falls back to disk when MinIO is unreachable. */
  storage: string;
  llm_configured: boolean;
  model: string;
  users: number;
}

/** Matches resume_ops.empty_resume() so the preview never reads undefined. */
export function emptyResume(): StructuredData {
  return {
    contact: { name: "", email: "", phone: "", location: "", links: [] },
    summary: { text: "" },
    experience: [],
    projects: [],
    education: [],
    skills: [],
    // Added when extras became a scored-but-optional fifth step. It was
    // missed here, and because this helper is the preview's fallback, the
    // omission broke `tsc -b` -- i.e. `npm run build`, not just the editor.
    extras: [],
  };
}

/** Mirrors DEFAULT_TOKENS in backend export_service.py. Used when a resume
 *  points at a template the API did not return. */
export const DEFAULT_DESIGN_TOKENS: DesignTokens = {
  font: "sans",
  heading: "rule",
  name_align: "left",
  accent: "#4737ff",
  density: "normal",
  caps: true,
  divider: false,
  header: "stacked",
  entry: "stacked",
  entry_lead: "role",
  layout: "single",
  entry_sep: " - ",
  edu: "stacked",
  dates_style: "plain",
  icons: false,
  edu_lead: "degree",
};
