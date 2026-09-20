"""Global RAG corpora: skill taxonomy and ATS rules.

These are the honest justification for the vector layer — a single resume is
small, but these corpora grow and are shared across every user.
"""

SKILL_TAXONOMY = [
    {"content": "kubernetes k8s container orchestration cluster pods helm",
     "doc_metadata": {"canonical": "Kubernetes", "aliases": ["k8s", "kube"]}},
    {"content": "docker containerisation containerization image registry oci",
     "doc_metadata": {"canonical": "Docker", "aliases": ["containers"]}},
    {"content": "terraform infrastructure as code iac hcl provisioning",
     "doc_metadata": {"canonical": "Terraform", "aliases": ["iac"]}},
    {"content": "aws amazon web services ec2 s3 lambda cloudformation",
     "doc_metadata": {"canonical": "AWS", "aliases": ["amazon web services"]}},
    {"content": "postgresql postgres relational database sql rdbms",
     "doc_metadata": {"canonical": "PostgreSQL", "aliases": ["postgres", "psql"]}},
    {"content": "python programming language django fastapi flask scripting",
     "doc_metadata": {"canonical": "Python", "aliases": ["py"]}},
    {"content": "golang go programming language concurrency goroutines",
     "doc_metadata": {"canonical": "Go", "aliases": ["golang"]}},
    {"content": "react javascript typescript frontend components hooks jsx",
     "doc_metadata": {"canonical": "React", "aliases": ["reactjs"]}},
    {"content": "typescript javascript static typing frontend tooling",
     "doc_metadata": {"canonical": "TypeScript", "aliases": ["ts"]}},
    {"content": "ci cd continuous integration delivery pipeline jenkins actions",
     "doc_metadata": {"canonical": "CI/CD", "aliases": ["ci", "cd", "pipelines"]}},
    {"content": "prometheus grafana observability metrics monitoring alerting",
     "doc_metadata": {"canonical": "Observability", "aliases": ["monitoring"]}},
    {"content": "gitops argocd flux declarative deployment progressive delivery",
     "doc_metadata": {"canonical": "GitOps", "aliases": ["argocd", "flux"]}},
    {"content": "slo sli sla error budget reliability incident response oncall",
     "doc_metadata": {"canonical": "SRE practice", "aliases": ["slo", "sli"]}},
    {"content": "airflow spark etl data pipeline warehouse batch processing",
     "doc_metadata": {"canonical": "Data Engineering", "aliases": ["etl"]}},
    {"content": "pytorch tensorflow machine learning model training inference llm",
     "doc_metadata": {"canonical": "Machine Learning", "aliases": ["ml", "ai"]}},
    {"content": "redis caching in-memory key value store pubsub",
     "doc_metadata": {"canonical": "Redis", "aliases": ["cache"]}},
    {"content": "microservices distributed systems api rest grpc service mesh",
     "doc_metadata": {"canonical": "Distributed Systems", "aliases": ["microservices"]}},
    {"content": "agile scrum kanban sprint backlog stakeholder roadmap",
     "doc_metadata": {"canonical": "Agile", "aliases": ["scrum"]}},
]

ATS_RULES = [
    {"content": "Start each bullet with a strong past-tense action verb such as led, built, "
                "reduced, migrated or automated rather than a passive phrase.",
     "doc_metadata": {"category": "experience", "rule": "action_verbs"}},
    {"content": "Quantify achievements with numbers, percentages, currency or time saved. "
                "A bullet without a metric reads as a duty, not an accomplishment.",
     "doc_metadata": {"category": "experience", "rule": "quantification"}},
    {"content": "Keep bullets under about 30 words. Long bullets bury the achievement and "
                "parse poorly in applicant tracking systems.",
     "doc_metadata": {"category": "format", "rule": "bullet_length"}},
    {"content": "Mirror the exact keyword spelling used in the job description. Many ATS "
                "filters match literally, so Kubernetes and K8s are not interchangeable.",
     "doc_metadata": {"category": "keywords", "rule": "exact_match"}},
    {"content": "Include a contact block with name, email, phone and location. Missing "
                "contact fields cause automatic rejection in some systems.",
     "doc_metadata": {"category": "contact", "rule": "completeness"}},
    {"content": "Write a professional summary of 30 to 60 words that states role, years of "
                "experience and one measurable outcome.",
     "doc_metadata": {"category": "summary", "rule": "length_and_impact"}},
    {"content": "Avoid tables, text boxes, headers, footers and multi-column layouts. Many "
                "parsers read them out of order or drop them entirely.",
     "doc_metadata": {"category": "format", "rule": "simple_layout"}},
    {"content": "List dates for every role in a consistent format. Gaps and missing dates "
                "are flagged by screening software.",
     "doc_metadata": {"category": "experience", "rule": "dates"}},
    {"content": "Group skills under clear labels such as Languages, Platform and Tools so "
                "both parsers and humans can scan them.",
     "doc_metadata": {"category": "skills", "rule": "grouping"}},
    {"content": "Never claim experience the resume does not support. Fabricated keywords "
                "fail at interview and damage credibility.",
     "doc_metadata": {"category": "integrity", "rule": "truthfulness"}},
]
