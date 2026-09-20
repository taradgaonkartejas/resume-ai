-- ResumeAI schema (PostgreSQL 16 + pgvector)
-- Generated from app/models.py by `python -m app.dbinit --sql`.
-- The ORM is the source of truth; init_db() applies this automatically.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE job_descriptions (
	id UUID NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	content TEXT NOT NULL, 
	extracted_keywords JSONB NOT NULL, 
	is_sample BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE templates (
	id UUID NOT NULL, 
	key VARCHAR(40) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	description VARCHAR(300) NOT NULL, 
	design_tokens JSONB NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key)
);

CREATE TABLE users (
	id UUID NOT NULL, 
	email VARCHAR(200) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	avatar_color VARCHAR(20) NOT NULL, 
	chat_tokens_left INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE agent_runs (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	thread_id VARCHAR(80) NOT NULL, 
	agent VARCHAR(40) NOT NULL, 
	task VARCHAR(80) NOT NULL, 
	model VARCHAR(80) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	tokens_in INTEGER NOT NULL, 
	tokens_out INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_agent_runs_user_id ON agent_runs (user_id);
CREATE INDEX ix_agent_runs_user_thread ON agent_runs (user_id, thread_id);

CREATE TABLE resumes (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	structured_data JSONB NOT NULL, 
	raw_text TEXT NOT NULL, 
	storage_key VARCHAR(400) NOT NULL, 
	parse_status VARCHAR(20) NOT NULL, 
	parse_note TEXT NOT NULL, 
	template_key VARCHAR(40) NOT NULL, 
	version_cursor INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(template_key) REFERENCES templates (key)
);
CREATE INDEX ix_resumes_user_id ON resumes (user_id);

CREATE TABLE vector_docs (
	id UUID NOT NULL, 
	user_id UUID, 
	corpus VARCHAR(40) NOT NULL, 
	resume_id UUID, 
	target_ref VARCHAR(80) NOT NULL, 
	placement VARCHAR(200) NOT NULL, 
	content TEXT NOT NULL, 
	doc_metadata JSONB NOT NULL, 
	embedding VECTOR(384) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_vector_docs_scope CHECK ((corpus IN ('skill_taxonomy','ats_rules') AND user_id IS NULL) OR (corpus NOT IN ('skill_taxonomy','ats_rules') AND user_id IS NOT NULL)), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_vector_docs_corpus ON vector_docs (corpus);
CREATE INDEX ix_vector_docs_resume_id ON vector_docs (resume_id);
CREATE INDEX ix_vector_docs_scope ON vector_docs (user_id, corpus);
CREATE INDEX ix_vector_docs_user_id ON vector_docs (user_id);

CREATE TABLE analysis_reports (
	id UUID NOT NULL, 
	resume_id UUID NOT NULL, 
	overall_score INTEGER NOT NULL, 
	category_scores JSONB NOT NULL, 
	role_tags JSONB NOT NULL, 
	trace JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE
);
CREATE INDEX ix_analysis_reports_resume_id ON analysis_reports (resume_id);

CREATE TABLE chat_messages (
	id UUID NOT NULL, 
	resume_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	content TEXT NOT NULL, 
	suggestion_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE
);
CREATE INDEX ix_chat_messages_resume_created ON chat_messages (resume_id, created_at);
CREATE INDEX ix_chat_messages_resume_id ON chat_messages (resume_id);

CREATE TABLE resume_versions (
	id UUID NOT NULL, 
	resume_id UUID NOT NULL, 
	seq INTEGER NOT NULL, 
	snapshot JSONB NOT NULL, 
	change_source VARCHAR(40) NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE
);
CREATE INDEX ix_resume_versions_resume_id ON resume_versions (resume_id);
CREATE UNIQUE INDEX ux_resume_versions_seq ON resume_versions (resume_id, seq);

CREATE TABLE tailoring_sessions (
	id UUID NOT NULL, 
	resume_id UUID NOT NULL, 
	job_description_id UUID NOT NULL, 
	match_percent FLOAT NOT NULL, 
	baseline_percent FLOAT NOT NULL, 
	matched_keywords JSONB NOT NULL, 
	gap_keywords JSONB NOT NULL, 
	all_keywords JSONB NOT NULL, 
	thread_id VARCHAR(80) NOT NULL, 
	graph_state VARCHAR(40) NOT NULL, 
	trace JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE, 
	FOREIGN KEY(job_description_id) REFERENCES job_descriptions (id) ON DELETE CASCADE
);
CREATE INDEX ix_tailoring_sessions_resume_id ON tailoring_sessions (resume_id);

CREATE TABLE suggestions (
	id UUID NOT NULL, 
	session_id UUID, 
	resume_id UUID NOT NULL, 
	origin VARCHAR(20) NOT NULL, 
	section VARCHAR(40) NOT NULL, 
	target_ref VARCHAR(80) NOT NULL, 
	placement VARCHAR(200) NOT NULL, 
	original_text TEXT NOT NULL, 
	suggested_text TEXT NOT NULL, 
	edited_text TEXT NOT NULL, 
	keywords JSONB NOT NULL, 
	reasoning TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	grounded BOOLEAN NOT NULL, 
	critic_notes TEXT NOT NULL, 
	revisions INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES tailoring_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE
);
CREATE INDEX ix_suggestions_resume_id ON suggestions (resume_id);
CREATE INDEX ix_suggestions_session_id ON suggestions (session_id);
CREATE INDEX ix_suggestions_session_status ON suggestions (session_id, status);
