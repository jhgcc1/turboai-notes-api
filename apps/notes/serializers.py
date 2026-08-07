from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.notes.models import Category, Note


class CategorySerializer(serializers.ModelSerializer[Category]):
    note_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "color", "note_count", "created_at")
        read_only_fields = ("id", "note_count", "created_at")


class NoteSerializer(serializers.ModelSerializer[Note]):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_color = serializers.CharField(source="category.color", read_only=True)

    class Meta:
        model = Note
        fields = (
            "id",
            "title",
            "body",
            "category",
            "category_name",
            "category_color",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "category_name", "category_color")

    def validate_category(self, category: Category) -> Category:
        request = self.context["request"]
        if category.user_id != request.user.id:
            raise serializers.ValidationError("Invalid category.")
        return category

    def create(self, validated_data: dict[str, Any]) -> Note:
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)
