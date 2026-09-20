"""User-scoped retrieval.

Scoping is enforced here and in VectorRepository — there is no code path that
returns another user's documents. With pgvector the ranking happens in SQL;
without it, cosine is computed in Python over the scoped candidate set.

Corpora:
    resume_bullets  user-scoped, per resume
    skill_taxonomy  global (user_id NULL)
    ats_rules       global (user_id NULL)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.ai.embeddings import cosine, embed, embed_many
from app.db import has_pgvector
from app.repositories.vector_repository import GLOBAL_CORPORA, VectorRepository
from app.services import resume_ops

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    content: str
    score: float
    target_ref: str = ""
    placement: str = ""
    doc_metadata: dict | None = None


class VectorStore:
    def __init__(self, repo: VectorRepository) -> None:
        self.repo = repo

    # ---------- indexing ----------
    def index_resume(
        self, user_id: uuid.UUID, resume_id: uuid.UUID, structured_data: dict
    ) -> int:
        """Re-index one resume's bullets. Idempotent: clears first."""
        self.repo.delete_for_resume(resume_id, user_id)
        rows = list(resume_ops.iter_bullets(structured_data))
        if not rows:
            return 0
        vectors = embed_many([text for _, _, text in rows])
        for (target_ref, placement, text), vector in zip(rows, vectors, strict=False):
            self.repo.add_doc(
                corpus="resume_bullets",
                content=text,
                embedding=vector,
                user_id=user_id,
                resume_id=resume_id,
                target_ref=target_ref,
                placement=placement,
            )
        return len(rows)

    def index_global(self, corpus: str, entries: list[dict]) -> int:
        """Seed a global corpus. entries: [{content, doc_metadata}]"""
        if corpus not in GLOBAL_CORPORA:
            raise ValueError(f"{corpus} is not a global corpus")
        existing = self.repo.candidates(corpus)
        if existing:
            return 0
        vectors = embed_many([e["content"] for e in entries])
        for entry, vector in zip(entries, vectors, strict=False):
            self.repo.add_doc(
                corpus=corpus,
                content=entry["content"],
                embedding=vector,
                user_id=None,
                doc_metadata=entry.get("doc_metadata", {}),
            )
        return len(entries)

    # ---------- retrieval ----------
    def search(
        self,
        query: str,
        corpus: str,
        user_id: uuid.UUID | None = None,
        resume_id: uuid.UUID | None = None,
        k: int = 5,
    ) -> list[Hit]:
        """Top-k by cosine similarity, always within the caller's scope."""
        if corpus not in GLOBAL_CORPORA and user_id is None:
            raise ValueError(f"Corpus {corpus!r} requires a user_id")

        if has_pgvector():
            try:
                return self._search_pgvector(query, corpus, user_id, resume_id, k)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pgvector search failed, using numpy path: %s", exc)

        return self._search_python(query, corpus, user_id, resume_id, k)

    def _search_pgvector(
        self,
        query: str,
        corpus: str,
        user_id: uuid.UUID | None,
        resume_id: uuid.UUID | None,
        k: int,
    ) -> list[Hit]:
        from sqlalchemy import select

        from app.models import VectorDoc

        vector = embed(query)
        stmt = select(
            VectorDoc, VectorDoc.embedding.cosine_distance(vector).label("distance")
        ).where(VectorDoc.corpus == corpus)

        # The scoping predicate is applied before ordering, never after.
        if corpus in GLOBAL_CORPORA:
            stmt = stmt.where(VectorDoc.user_id.is_(None))
        else:
            stmt = stmt.where(VectorDoc.user_id == user_id)
            if resume_id is not None:
                stmt = stmt.where(VectorDoc.resume_id == resume_id)

        stmt = stmt.order_by("distance").limit(k)
        return [
            Hit(
                content=doc.content,
                score=1.0 - float(distance),
                target_ref=doc.target_ref,
                placement=doc.placement,
                doc_metadata=doc.doc_metadata,
            )
            for doc, distance in self.repo.db.execute(stmt).all()
        ]

    def _search_python(
        self,
        query: str,
        corpus: str,
        user_id: uuid.UUID | None,
        resume_id: uuid.UUID | None,
        k: int,
    ) -> list[Hit]:
        docs = self.repo.candidates(corpus, user_id=user_id, resume_id=resume_id)
        if not docs:
            return []
        vector = embed(query)
        scored = [
            Hit(
                content=doc.content,
                score=cosine(vector, list(doc.embedding or [])),
                target_ref=doc.target_ref,
                placement=doc.placement,
                doc_metadata=doc.doc_metadata,
            )
            for doc in docs
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]
