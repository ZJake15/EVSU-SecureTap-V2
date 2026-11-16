"""Dashboard sign-in tokens, with one extra rule: someone whose password was
set by an Admin (AdminProfile.must_change_password - a new account, or a
forgotten password reset on the Accounts page) can use their token for one
thing only, choosing their own password. Checked on every request here, on
the server, so the rule holds no matter what calls the API - the dashboard's
"Choose a new password" screen is only the friendly side of it."""

from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

# What a "must change password" token may still reach.
ALLOWED_PATHS = ("/api/auth/change-password",)


class PasswordChangeRequired(exceptions.PermissionDenied):
    default_detail = "Choose a new password before using the dashboard."
    default_code = "password_change_required"


class SecureTapJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, _token = result
        profile = getattr(user, "admin_profile", None)
        if profile is not None and profile.must_change_password and request.path not in ALLOWED_PATHS:
            # "code" lets the dashboard switch to its Choose-a-new-password
            # screen - e.g. an Admin reset the password while they were in.
            raise PasswordChangeRequired({"detail": PasswordChangeRequired.default_detail,
                                          "code": PasswordChangeRequired.default_code})
        return result


def end_sessions(user):
    """Signs this account out of the dashboard everywhere (its refresh tokens
    stop working; an access token runs out within the hour) - after a
    password reset or change, so whoever knew the old one is out."""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
