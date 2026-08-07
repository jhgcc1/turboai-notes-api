"""Notes and categories API."""

from __future__ import annotations

from django.db.models import Count
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notes.models import Category, Note
from apps.notes.permissions import IsOwner
from apps.notes.serializers import CategorySerializer, NoteSerializer
from apps.notes.services import ensure_default_categories


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer

    def get_queryset(self):
        ensure_default_categories(self.request.user)
        return (
            Category.objects.filter(user=self.request.user)
            .annotate(note_count=Count("notes"))
            .order_by("name")
        )


class NoteListCreateView(generics.ListCreateAPIView):
    serializer_class = NoteSerializer

    def get_queryset(self):
        qs = Note.objects.filter(user=self.request.user).select_related("category")
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs


class NoteDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Note.objects.filter(user=self.request.user).select_related("category")


class SeedStagingView(APIView):
    """Seed demo notes — blocked in production."""

    def post(self, request: Request) -> Response:
        from django.conf import settings

        if settings.ENVIRONMENT == "production":
            return Response(
                {"detail": "Seeding is not allowed in production."},
                status=status.HTTP_403_FORBIDDEN,
            )
        ensure_default_categories(request.user)
        cat = Category.objects.filter(user=request.user, name="Random Thoughts").first()
        assert cat is not None
        note, created = Note.objects.get_or_create(
            user=request.user,
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
