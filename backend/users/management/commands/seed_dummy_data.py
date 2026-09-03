import numpy as np
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import AdminProfile
from users.models import FaceEmbedding, Person

# username, role, assigned_gate_location (only meaningful for Security
# Officer), password - change these passwords after first login.
ADMIN_ACCOUNTS = [
    ("admin_demo", AdminProfile.Role.ADMIN, "", "ChangeMe123!"),
    ("saso_demo", AdminProfile.Role.SASO, "", "ChangeMe123!"),
    ("guard_demo", AdminProfile.Role.SECURITY_OFFICER, "Main Gate", "ChangeMe123!"),
]

DUMMY_PEOPLE = [
    ("Juan Dela Cruz", Person.Role.STUDENT, "2021-00123", "NFC-0001", "BS Computer Science"),
    ("Maria Santos", Person.Role.STUDENT, "2021-00456", "NFC-0002", "BS Information Technology"),
    ("Pedro Reyes", Person.Role.STAFF, "EMP-0089", "NFC-0003", "Registrar's Office"),
    ("Ana Villanueva", Person.Role.STUDENT, "2022-00789", "NFC-0004", "BS Civil Engineering"),
    ("Jose Ramirez", Person.Role.STAFF, "EMP-0102", "NFC-0005", "Security Office"),
]


class Command(BaseCommand):
    help = "Seeds demo dashboard accounts and dummy Person/FaceEmbedding rows for local testing."

    def handle(self, *args, **options):
        with transaction.atomic():
            self._seed_admin_accounts()
            self._seed_dummy_people()
        self.stdout.write(self.style.SUCCESS("Seed data loaded."))

    def _seed_admin_accounts(self):
        for username, role, gate, password in ADMIN_ACCOUNTS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "is_staff": role == AdminProfile.Role.ADMIN,
                    "is_superuser": role == AdminProfile.Role.ADMIN,
                },
            )
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(f"  Created login: {username} / {password} (role={role})")
            AdminProfile.objects.update_or_create(
                user=user, defaults={"role": role, "assigned_gate_location": gate},
            )

    def _seed_dummy_people(self):
        self.stdout.write(
            self.style.WARNING(
                "Dummy Person rows get RANDOM face embeddings (no real photo behind them) - "
                "they're only good for exercising listing/logs/reports. Register at least one "
                "real person with a real photo (via the dashboard or /api/users) to test actual "
                "face verification end-to-end."
            )
        )
        rng = np.random.default_rng(seed=42)
        for full_name, role, student_id, nfc_id, department in DUMMY_PEOPLE:
            person, created = Person.objects.get_or_create(
                student_or_employee_id=student_id,
                defaults={
                    "full_name": full_name,
                    "role": role,
                    "nfc_id": nfc_id,
                    "department_or_course": department,
                },
            )
            if created:
                # ArcFace embeddings are unit-normalized - matching (a plain
                # dot product as cosine similarity) assumes that, so a fake
                # embedding has to be normalized too, not just random.
                fake_vector = rng.normal(size=512)
                fake_embedding = (fake_vector / np.linalg.norm(fake_vector)).tolist()
                FaceEmbedding.objects.create(person=person, embedding=fake_embedding)
                self.stdout.write(f"  Created dummy person: {full_name} ({student_id})")
