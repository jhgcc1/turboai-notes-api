from django.urls import path

from apps.notes.views import CategoryListView, NoteDetailView, NoteListCreateView, SeedStagingView

urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path("notes/", NoteListCreateView.as_view(), name="note-list"),
    path("notes/<int:pk>/", NoteDetailView.as_view(), name="note-detail"),
    path("seed/", SeedStagingView.as_view(), name="seed-staging"),
]
