from __future__ import annotations

from django.urls import path

from apps.observability.views import ClientErrorView

urlpatterns = [
    path("client-error/", ClientErrorView.as_view(), name="observability-client-error"),
]
