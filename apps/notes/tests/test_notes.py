"""Notes API tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.notes.models import Category, Note
from apps.notes.services import ensure_default_categories


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="owner@example.com",
        email="owner@example.com",
        password="StrongPass123!",
    )


@pytest.fixture
def other(db) -> User:
    return User.objects.create_user(
        username="other@example.com",
        email="other@example.com",
        password="StrongPass123!",
    )


@pytest.fixture
def auth_api(api: APIClient, user: User) -> APIClient:
    refresh = RefreshToken.for_user(user)
    api.cookies["access_token"] = str(refresh.access_token)
    return api


@pytest.mark.django_db
def test_categories_and_notes_crud(auth_api: APIClient, user: User) -> None:
    cats = auth_api.get(reverse("category-list"))
    assert cats.status_code == status.HTTP_200_OK
    assert len(cats.data) == 3

    category_id = cats.data[0]["id"]
    create = auth_api.post(
        reverse("note-list"),
        {"title": "Grocery List", "body": "- Milk", "category": category_id},
        format="json",
    )
    assert create.status_code == status.HTTP_201_CREATED
    note_id = create.data["id"]

    listed = auth_api.get(reverse("note-list"))
    assert listed.status_code == status.HTTP_200_OK
    assert len(listed.data) == 1

    filtered = auth_api.get(reverse("note-list"), {"category": category_id})
    assert len(filtered.data) == 1

    detail = auth_api.get(reverse("note-detail", args=[note_id]))
    assert detail.data["title"] == "Grocery List"

    patch = auth_api.patch(
        reverse("note-detail", args=[note_id]),
        {"title": "Updated"},
        format="json",
    )
    assert patch.data["title"] == "Updated"

    delete = auth_api.delete(reverse("note-detail", args=[note_id]))
    assert delete.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_cannot_use_others_category(auth_api: APIClient, other: User) -> None:
    other_cat = Category.objects.create(user=other, name="Secret", color="#000000")
    res = auth_api.post(
        reverse("note-list"),
        {"title": "Hack", "body": "x", "category": other_cat.id},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_cannot_access_others_note(auth_api: APIClient, user: User, other: User) -> None:
    cat = ensure_default_categories(other)[0]
    # ensure_default returns only newly created; fetch one
    cat = Category.objects.filter(user=other).first()
    assert cat is not None
    note = Note.objects.create(user=other, category=cat, title="Private", body="nope")
    res = auth_api.get(reverse("note-detail", args=[note.id]))
    assert res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_seed_allowed_and_blocked(auth_api: APIClient, settings) -> None:
    settings.ENVIRONMENT = "staging"
    first = auth_api.post(reverse("seed-staging"), format="json")
    assert first.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    second = auth_api.post(reverse("seed-staging"), format="json")
    assert second.status_code == status.HTTP_200_OK

    settings.ENVIRONMENT = "production"
    blocked = auth_api.post(reverse("seed-staging"), format="json")
    assert blocked.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_model_str(user: User) -> None:
    cat = Category.objects.create(user=user, name="School", color="#F3E3B5")
    note = Note.objects.create(user=user, category=cat, title="", body="x")
    assert "School" in str(cat)
    assert "Note" in str(note)
    note.title = "Hello"
    note.save()
    assert str(note) == "Hello"


@pytest.mark.django_db
def test_is_owner_permission(user: User, other: User) -> None:
    from apps.notes.permissions import IsOwner
    from unittest.mock import MagicMock

    cat = Category.objects.create(user=user, name="Personal", color="#9EBAB0")
    note = Note.objects.create(user=user, category=cat, title="t", body="b")
    perm = IsOwner()
    req = MagicMock()
    req.user = user
    assert perm.has_object_permission(req, None, note) is True
    req.user = other
    assert perm.has_object_permission(req, None, note) is False
