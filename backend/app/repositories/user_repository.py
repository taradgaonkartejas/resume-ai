import uuid

from sqlalchemy import select

from app.models import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    def list_all(self) -> list[User]:
        return list(self.db.scalars(select(User).order_by(User.created_at)))

    def get(self, user_id: uuid.UUID) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))

    def count(self) -> int:
        return len(list(self.db.scalars(select(User.id))))

    def decrement_tokens(self, user: User, amount: int = 1) -> User:
        user.chat_tokens_left = max(0, user.chat_tokens_left - amount)
        return user
