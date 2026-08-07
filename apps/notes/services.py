"""Default category seeding helpers."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from apps.notes.models import Category

DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("Random Thoughts", "#E9A680"),
    ("School", "#F3E3B5"),
    ("Personal", "#9EBAB0"),
]


def ensure_default_categories(user: AbstractBaseUser) -> list[Category]:
    created: list[Category] = []
    for name, color in DEFAULT_CATEGORIES:
        obj, was_created = Category.objects.get_or_create(
            user=user,
            name=name,
            defaults={"color": color},
        )
        if was_created:
            created.append(obj)
    return created
