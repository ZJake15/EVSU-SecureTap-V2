"""Creates the first Admin account on a new copy - for the launcher's
first-run setup, so a fresh install never ships with a default password.

    python manage.py create_first_admin --username jdelacruz --first-name Juan --last-name "Dela Cruz"

The password is read from the first line of standard input (never the
command line, where other programs could see it). It has to pass the same
rules as a password set on the dashboard. Refuses once an active Admin
exists - after that, accounts are made on the dashboard's Accounts page.
"""

import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import AdminProfile
from audit.utils import log_action


class Command(BaseCommand):
    help = "Creates the first Admin account (password read from standard input)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    def handle(self, *args, **options):
        if AdminProfile.objects.filter(role=AdminProfile.Role.ADMIN, user__is_active=True).exists():
            raise CommandError("An Admin account already exists - add more accounts on the dashboard's Accounts page.")
        username = options["username"].strip()
        if not username:
            raise CommandError("Enter a username.")
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"The username {username!r} is already taken.")
        # Raw bytes as UTF-8 (what the setup window sends), not the console's
        # code page - otherwise "ñ" would be saved as something else than what
        # was typed. utf-8-sig also drops the invisible byte-order mark some
        # Windows shells put in front of piped text, which would otherwise
        # become part of the password.
        password = sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\r\n")
        if not password:
            raise CommandError("Enter a password.")
        candidate = User(username=username, first_name=options["first_name"], last_name=options["last_name"])
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        with transaction.atomic():
            # Same flags the dashboard's Accounts page gives an Admin (see
            # accounts/serializers.py).
            user = User.objects.create(
                username=username, first_name=options["first_name"], last_name=options["last_name"],
                is_staff=True, is_superuser=True,
            )
            user.set_password(password)
            user.save()
            AdminProfile.objects.create(user=user, role=AdminProfile.Role.ADMIN)
            log_action(None, "account_created", target_description=f"Account {username} (admin)",
                       detail={"via": "first-run setup"})
        self.stdout.write(self.style.SUCCESS(f"Admin account {username} created."))
