from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import AdminProfile


class RoleTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Embeds the dashboard role/full name/gate in the JWT so the frontend can route by
    role (and, for a Security Officer, know which gate they're scoped to) without a
    separate profile lookup."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        try:
            profile = user.admin_profile
            role = profile.role
            gate = profile.assigned_gate_location or None
        except AdminProfile.DoesNotExist:
            role = None
            gate = None
        token["role"] = role
        token["username"] = user.username
        token["full_name"] = user.get_full_name() or user.username
        token["gate_location"] = gate
        # The dashboard shows only "Choose a new password" until this is
        # false (the server enforces it - accounts/authentication.py).
        token["must_change_password"] = bool(getattr(getattr(user, "admin_profile", None), "must_change_password",
                                                     False))
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        if not hasattr(self.user, "admin_profile"):
            raise serializers.ValidationError("This account has no dashboard role assigned.")
        return data


class DashboardAccountSerializer(serializers.ModelSerializer):
    """Account Management (Admin-only): create/edit/deactivate SASO and
    Security Officer dashboard logins. Deliberately separate from Person -
    these are STAFF DASHBOARD LOGINS (User + AdminProfile), not enrolled
    students/staff in the gate-recognition sense; a Person can exist with no
    dashboard account at all (most students/staff never log into anything),
    and a dashboard account has no Person record backing it.

    Wraps both User and AdminProfile in one request/response, the same "one
    serializer, two models" pattern PersonSerializer already uses for
    Person + FaceEmbedding.
    """

    username = serializers.CharField(source="user.username")
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False, allow_blank=True)
    last_name = serializers.CharField(source="user.last_name", required=False, allow_blank=True)
    is_active = serializers.BooleanField(source="user.is_active", required=False, default=True)
    # write-only: never echoed back, and optional on update (blank = keep the
    # existing password) - same "leave blank to keep the current one" idea
    # PersonSerializer's profile_picture already uses for a different field.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # Their own staff record in User Management, by employee ID - its face
    # photos let them sign in at the gate monitor by face. Blank unlinks it.
    staff_record_id = serializers.CharField(write_only=True, required=False, allow_blank=True)
    staff_record = serializers.SerializerMethodField()
    # With a password typed in: make them choose their own at next sign-in
    # (the dashboard's checkbox, on by default). Ignored for your own account.
    require_password_change = serializers.BooleanField(write_only=True, required=False)
    locked = serializers.SerializerMethodField()

    class Meta:
        model = AdminProfile
        fields = [
            "id", "username", "first_name", "last_name", "full_name",
            "role", "assigned_gate_location", "staff_card_id", "staff_record_id", "staff_record",
            "is_active", "password", "require_password_change", "must_change_password", "locked", "created_at",
        ]
        read_only_fields = ["id", "created_at", "must_change_password"]

    def get_locked(self, profile):
        """Locked out by too many wrong passwords right now (Settings: Failed
        login lockout) - saving a new password unlocks it."""
        from . import lockout

        return bool(lockout.locked_message(profile))

    def validate_password(self, value):
        """The same rules as everywhere else (settings.AUTH_PASSWORD_VALIDATORS:
        at least 10 characters, not a common password, not like the username).
        Blank on an edit keeps the current password."""
        if not value:
            return value
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        user = self.instance.user if self.instance is not None else User(
            username=self.initial_data.get("username", ""), first_name=self.initial_data.get("first_name", ""),
            last_name=self.initial_data.get("last_name", ""))
        try:
            validate_password(value, user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages)) from error
        return value

    def _own_account(self, user):
        request = self.context.get("request")
        return request is not None and request.user.pk == user.pk

    def get_staff_record(self, profile):
        person = profile.staff_person
        if person is None:
            return None
        return {"id_number": person.student_or_employee_id, "name": person.full_name,
                "photos": person.face_embeddings.count(), "is_active": person.is_active}

    def validate_staff_record_id(self, value):
        """The employee ID of a staff record (not a student's), not already
        linked to another account - one face, one guard."""
        value = (value or "").strip()
        if not value:
            return None
        from users.models import Person

        person = Person.objects.filter(student_or_employee_id=value).first()
        if person is None:
            raise serializers.ValidationError("No one in User Management has this ID.")
        if person.role != Person.Role.STAFF:
            raise serializers.ValidationError("This ID belongs to a student - link a staff record.")
        others = AdminProfile.objects.filter(staff_person=person)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("Another account is already linked to this staff record.")
        return person

    def validate_staff_card_id(self, value):
        """A staff ID card signs its owner in at the gate monitor, so it must
        never be another account's card or a student's - one tap, one
        meaning."""
        value = (value or "").strip()
        if not value:
            return ""
        others = AdminProfile.objects.filter(staff_card_id=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("Another account already uses this card.")
        from users.models import Person

        if Person.objects.filter(nfc_id=value).exists():
            raise serializers.ValidationError("This card belongs to a registered student or staff member.")
        return value

    def validate(self, attrs):
        role = attrs.get("role", getattr(self.instance, "role", None))
        gate = attrs.get("assigned_gate_location", "")
        if role != AdminProfile.Role.SECURITY_OFFICER and gate:
            raise serializers.ValidationError(
                {"assigned_gate_location": "Only a Security Officer account can have an assigned gate."}
            )
        if "staff_record_id" in attrs:
            attrs["staff_person"] = attrs.pop("staff_record_id")
        return attrs

    def create(self, validated_data):
        user_data = validated_data.pop("user")
        password = validated_data.pop("password", None)
        # A new account's first password is the Admin's - by default the
        # person picks their own the first time they sign in.
        validated_data["must_change_password"] = validated_data.pop("require_password_change", True)
        if not password:
            raise serializers.ValidationError({"password": "A password is required for a new account."})
        user = User.objects.create(
            username=user_data["username"],
            first_name=user_data.get("first_name", ""),
            last_name=user_data.get("last_name", ""),
            is_active=user_data.get("is_active", True),
            # Mirrors seed_dummy_data's own convention: Admin accounts get
            # Django staff+superuser (so the /admin/ panel stays available as
            # a break-glass path); SASO/Security Officer get neither - this
            # app's own role field is what actually gates the dashboard API,
            # Django's is_staff/is_superuser are unrelated to it.
            is_staff=validated_data.get("role") == AdminProfile.Role.ADMIN,
            is_superuser=validated_data.get("role") == AdminProfile.Role.ADMIN,
        )
        user.set_password(password)
        user.save()
        return AdminProfile.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        password = validated_data.pop("password", None)
        require_change = validated_data.pop("require_password_change", True)
        user = instance.user
        for field in ("username", "first_name", "last_name", "is_active"):
            if field in user_data:
                setattr(user, field, user_data[field])
        if password:
            # A forgotten password: the new one also lifts a lockout, and
            # (unless it's your own) is only good for choosing theirs.
            from . import lockout

            user.set_password(password)
            own = self._own_account(user)
            instance.password_reset = {"was_locked": bool(lockout.locked_message(instance)), "own": own,
                                       "must_change_password": bool(require_change and not own)}
            instance.failed_login_count = 0
            instance.locked_until = None
            instance.must_change_password = bool(require_change and not own)
        if "role" in validated_data:
            user.is_staff = validated_data["role"] == AdminProfile.Role.ADMIN
            user.is_superuser = validated_data["role"] == AdminProfile.Role.ADMIN
        user.save()

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance
