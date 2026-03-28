"""Flask-Login user model."""
from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, id, orcid_id, name, email, is_admin):
        self.id       = id
        self.orcid_id = orcid_id
        self.name     = name
        self.email    = email
        self.is_admin = bool(is_admin)

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            orcid_id=row["orcid_id"],
            name=row["name"],
            email=row["email"],
            is_admin=row["is_admin"],
        )
