from sqlalchemy.orm import Session


class BaseRepository:
    """Holds the request-scoped Session.

    Repositories never commit. The service layer owns the transaction so a
    multi-table write is a single unit of work.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, instance):
        self.db.add(instance)
        return instance

    def flush(self) -> None:
        """Assign primary keys without ending the transaction."""
        self.db.flush()
