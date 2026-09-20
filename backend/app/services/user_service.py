import uuid

from app.models import User
from app.repositories.user_repository import UserRepository
from app.services.exceptions import NotFoundError


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self.users = users

    def list_users(self):
        return self.users.list_all()

    def get(self, user_id: uuid.UUID) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError(str(user_id))
        return user
