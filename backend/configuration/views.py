from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin
from users import occlusion_utils

from . import store


class SettingsView(APIView):
    """The dashboard's Settings page. Admin only - SASO and Security
    Officer accounts get 403 here on the server, whatever the menu shows.

    GET: every setting with its value, limits and wording (store.describe).
    PATCH {"values": {key: value, ...}, "confirmed": true|false}: saves the
    changes all-or-nothing. 400 with per-setting "errors" if any value is not
    allowed; 409 with "confirmation_required" if a risky change was sent
    without "confirmed": true (the page then asks the admin and resends).
    """

    permission_classes = [IsAdmin]

    @staticmethod
    def _payload():
        return store.describe(status={"covered_face_mode": occlusion_utils.active_mode_description()})

    def get(self, request):
        return Response(self._payload())

    def patch(self, request):
        changes = request.data.get("values")
        if not isinstance(changes, dict) or not changes:
            return Response({"detail": "Nothing to save."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            store.update(changes, request.user, confirmed=request.data.get("confirmed") is True)
        except store.ValidationFailed as exc:
            return Response(
                {"detail": "Some values aren't allowed.", "errors": exc.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except store.ConfirmationRequired as exc:
            return Response(
                {"detail": "These changes need confirming.", "confirmation_required": exc.items},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self._payload())


class SessionPolicyView(APIView):
    """The few settings every signed-in dashboard user's page needs - not
    the whole Settings page, which is Admin only: when to log them out for
    inactivity (0 = automatic logout off), and whether the Add Person form
    offers single-photo registration (the server enforces that too)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        minutes = store.get("idle_logout_minutes") if store.get("idle_logout_enabled") else 0
        return Response({"idle_logout_minutes": minutes, "allow_single_photo": store.get("allow_single_photo")})
