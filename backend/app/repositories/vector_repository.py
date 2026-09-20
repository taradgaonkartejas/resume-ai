import uuid

from sqlalchemy import delete, select

from app.models import VectorDoc
from app.repositories.base import BaseRepository

GLOBAL_CORPORA = ("skill_taxonomy", "ats_rules")


class VectorRepository(BaseRepository):
    """User-scoped retrieval.

    A user-owned corpus is ALWAYS filtered by user_id. Global corpora carry
    user_id NULL and are readable by everyone. There is no code path that
    returns another user's rows.
    """

    def add_doc(
        self,
        corpus: str,
        content: str,
        embedding: list[float],
        user_id: uuid.UUID | None = None,
        resume_id: uuid.UUID | None = None,
        target_ref: str = "",
        placement: str = "",
        doc_metadata: dict | None = None,
    ) -> VectorDoc:
        if corpus in GLOBAL_CORPORA and user_id is not None:
            raise ValueError(f"Global corpus {corpus!r} must have user_id=None")
        if corpus not in GLOBAL_CORPORA and user_id is None:
            raise ValueError(f"Corpus {corpus!r} requires a user_id")
        doc = VectorDoc(
            corpus=corpus,
            content=content,
            embedding=embedding,
            user_id=user_id,
            resume_id=resume_id,
            target_ref=target_ref,
            placement=placement,
            doc_metadata=doc_metadata or {},
        )
        self.db.add(doc)
        self.db.flush()
        return doc

    def candidates(
        self,
        corpus: str,
        user_id: uuid.UUID | None = None,
        resume_id: uuid.UUID | None = None,
    ) -> list[VectorDoc]:
        stmt = select(VectorDoc).where(VectorDoc.corpus == corpus)
        if corpus in GLOBAL_CORPORA:
            stmt = stmt.where(VectorDoc.user_id.is_(None))
        else:
            if user_id is None:
                raise ValueError(f"Corpus {corpus!r} requires a user_id")
            stmt = stmt.where(VectorDoc.user_id == user_id)
            if resume_id is not None:
                stmt = stmt.where(VectorDoc.resume_id == resume_id)
        return list(self.db.scalars(stmt))

    def delete_for_resume(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> int:
        result = self.db.execute(
            delete(VectorDoc).where(
                VectorDoc.resume_id == resume_id, VectorDoc.user_id == user_id
            )
        )
        return int(result.rowcount or 0)
