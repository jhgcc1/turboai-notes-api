from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOwner(BasePermission):
    def has_object_permission(self, request, view, obj) -> bool:  # type: ignore[no-untyped-def]
        return getattr(obj, "user_id", None) == getattr(request.user, "id", None)
