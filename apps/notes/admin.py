from django.contrib import admin

from apps.notes.models import Category, Note


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "color", "user", "created_at")
    list_filter = ("name",)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("title", "category", "user", "updated_at")
    list_filter = ("category",)
