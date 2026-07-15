from django.conf import settings
from django.db import models


class AdminProfile(models.Model):
    """Extends Django's built-in User with the dashboard role (admin/security/it).

    This stands in for the spec's "admin_accounts" table: rather than a hand-rolled
    username/password_hash table, we reuse Django's battle-tested auth_user (hashed
    with bcrypt - see PASSWORD_HASHERS in settings.py) and just attach a role to it.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        SECURITY = "security", "Security"
        IT = "it", "IT"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"
