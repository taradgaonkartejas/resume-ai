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
  /** Skills grid width, 1-3 columns. */
  skill_columns: number;
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
  /** "" | "rewrite" | "rerank" — which one-click action applies, if any. */
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
}

/** Scoring categories. Every Finding belongs to exactly one. */
export type CategoryKey = "contact" | "summary" | "experience" | "format";

/**
 * Step ids. A superset of CategoryKey: "extras" is navigable but unscored and
 * NEVER emits findings, which is why Finding.category stays CategoryKey.
 */
export type StepKey = CategoryKey | "extras";

export interface AnalysisOut {
  id: string;
  resume_id: string;
  overall_score: number;
  category_scores: Record<CategoryKey, CategoryScore>;
  role_tags: string[];
  created_at: string;
}

/* ---------- tailoring ---------- */

export interface TailorIn {
  jd_title?: string;
  jd_content: string;
  /**
   * Tailoring forks the resume by default, so the base stays pristine and one
   * base can serve many applications. The session's `resume_id` is the CHILD.
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
export type SuggestionStatus = "pending" | "accepted" | "rejected";

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
}

export interface SuggestionBuckets {
  active: SuggestionOut[];
  matched: SuggestionOut[];
  rejected: SuggestionOut[];
}

/* ---------- chat ---------- */

export interface ChatMessageOut {
  id: string;
  resume_id: string;
  role: string;
  content: string;
  suggestion_id: string | null;
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
  skill_columns: 1,
};
