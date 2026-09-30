from rest_framework.permissions import BasePermission


def get_staff(user):
    """The Staff profile for an authenticated user, or None."""
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "staff", None)


class IsCentreStaff(BasePermission):
    """Authenticated user with a Staff profile at an active centre."""

    message = "You must be a staff member of an active centre."

    def has_permission(self, request, view):
        staff = get_staff(request.user)
        return bool(staff and request.user.is_active and staff.centre.is_active)


class IsManager(IsCentreStaff):
    message = "Only managers can perform this action."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.staff.is_manager


class CentreScopedMixin:
    """Restricts a view's queryset to the requesting staff member's centre.

    Every centre-owned model has a `centre` FK. Scoping the queryset (rather than
    checking after lookup) means another centre's object is a 404, not a 403, so
    IDs from other centres can't even be probed.
    """

    centre_field = "centre"

    @property
    def staff(self):
        return self.request.user.staff

    def get_queryset(self):
        return super().get_queryset().filter(**{self.centre_field: self.staff.centre})
