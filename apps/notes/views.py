"""Notes and categories API."""

from __future__ import annotations

from typing import cast

from django.contrib.auth.models import User
from django.db.models import Count, QuerySet
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notes.models import Category, Note
from apps.notes.permissions import IsOwner
from apps.notes.serializers import CategorySerializer, NoteSerializer
from apps.notes.services import ensure_default_categories


def _user(request: Request) -> User:
    """Narrow ``request.user`` for views gated by ``IsAuthenticated``.

    DRF/django-stubs type ``request.user`` as ``AbstractBaseUser |
    AnonymousUser`` since it doesn't know about the view's permission
    classes; every view here requires authentication (via
    ``DEFAULT_PERMISSION_CLASSES`` or an explicit ``IsAuthenticated``), so at
    runtime it is always a real, authenticated ``User`` by the time these
    methods run.
    """
    return cast(User, request.user)


class CategoryListView(generics.ListAPIView[Category]):
    serializer_class = CategorySerializer

    def get_queryset(self) -> QuerySet[Category]:
        user = _user(self.request)
        ensure_default_categories(user)
        return (
            Category.objects.filter(user=user).annotate(note_count=Count("notes")).order_by("name")
        )


class NoteListCreateView(generics.ListCreateAPIView[Note]):
    serializer_class = NoteSerializer

    def get_queryset(self) -> QuerySet[Note]:
        qs = Note.objects.filter(user=_user(self.request)).select_related("category")
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs


class NoteDetailView(generics.RetrieveUpdateDestroyAPIView[Note]):
    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self) -> QuerySet[Note]:
        return Note.objects.filter(user=_user(self.request)).select_related("category")


class SeedStagingView(APIView):
    """Seed demo notes — blocked in production."""

    def post(self, request: Request) -> Response:
        from django.conf import settings

        if settings.ENVIRONMENT == "production":
            return Response(
                {"detail": "Seeding is not allowed in production."},
                status=status.HTTP_403_FORBIDDEN,
            )
        user = _user(request)
        ensure_default_categories(user)
        cat = Category.objects.filter(user=user, name="Random Thoughts").first()
        assert cat is not None
        note, created = Note.objects.get_or_create(
            user=user,
            title="Grocery List",
            defaults={
                "category": cat,
                "body": "- Milk\n- Eggs\n- Bread\n- Bananas\n- Spinach",
            },
        )
        return Response(
            {"seeded": created, "note_id": note.id},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
