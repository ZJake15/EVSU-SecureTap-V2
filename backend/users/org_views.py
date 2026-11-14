"""EVSU's schools, offices and departments (users.models.OrgUnit / Department):
read by every dashboard role (the registration form, filters), changed only by
an Admin in Settings > Schools, Offices & Departments. Every change is written
to the Audit Log. Nothing is ever deleted - an entry no longer used is hidden,
so the people already in it keep it."""

from django.db import IntegrityError
from django.db.models import Count
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsAnyDashboardRole
from audit.models import AuditLogEntry
from audit.utils import log_action

from .models import Department, OrgUnit, Person


def _error(message):
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _name(value):
    return " ".join(str(value or "").split())[:150]


def org_lists():
    units = OrgUnit.objects.annotate(people_count=Count("people")).prefetch_related("departments")
    return {
        "units": [
            {
                "id": unit.id, "name": unit.name, "kind": unit.kind, "group": unit.group,
                "sort_order": unit.sort_order, "is_active": unit.is_active, "people": unit.people_count,
                "departments": [
                    {"id": dept.id, "name": dept.name, "is_active": dept.is_active, "sort_order": dept.sort_order}
                    for dept in unit.departments.all()
                ],
            }
            for unit in units
        ],
        "staff_types": [{"value": value, "label": label} for value, label in Person.StaffType.choices],
        "year_levels": [{"value": value, "label": label} for value, label in Person.YearLevel.choices],
    }


def _audit(request, what):
    log_action(request.user, AuditLogEntry.Action.ORG_LIST_CHANGED, target_description=what[:255])


class OrgUnitListView(APIView):
    """GET: the lists. POST (Admin): a new school or office {name, kind, group}."""

    def get_permissions(self):
        return [IsAdmin()] if self.request.method == "POST" else [IsAnyDashboardRole()]

    def get(self, request):
        return Response(org_lists())

    def post(self, request):
        name, kind = _name(request.data.get("name")), request.data.get("kind")
        if not name:
            return _error("Type the name.")
        if kind not in OrgUnit.Kind.values:
            return _error("Choose school or office.")
        group = _name(request.data.get("group")) or ("Schools" if kind == OrgUnit.Kind.SCHOOL else "")
        last = OrgUnit.objects.filter(kind=kind).order_by("-sort_order").first()
        try:
            OrgUnit.objects.create(name=name, kind=kind, group=group, sort_order=(last.sort_order + 1) if last else 0)
        except IntegrityError:
            return _error(f"\"{name}\" is already in the list.")
        _audit(request, f"Added {kind} \"{name}\"")
        return Response(org_lists(), status=status.HTTP_201_CREATED)


class OrgUnitDetailView(APIView):
    """PATCH (Admin): rename, regroup, hide or show a school or office."""

    permission_classes = [IsAdmin]

    def patch(self, request, unit_id):
        unit = OrgUnit.objects.filter(id=unit_id).first()
        if unit is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        changes = []
        if "name" in request.data:
            name = _name(request.data["name"])
            if not name:
                return _error("The name can't be empty.")
            if OrgUnit.objects.exclude(id=unit.id).filter(name=name).exists():
                return _error(f"\"{name}\" is already in the list.")
            if name != unit.name:
                changes.append(f"renamed \"{unit.name}\" to \"{name}\"")
                unit.name = name
        if "group" in request.data:
            unit.group = _name(request.data["group"])
        if "is_active" in request.data:
            active = bool(request.data["is_active"])
            if active != unit.is_active:
                changes.append(f"{'showed' if active else 'hid'} \"{unit.name}\"")
                unit.is_active = active
        unit.save()
        if changes:
            _audit(request, "; ".join(changes))
        return Response(org_lists())


class DepartmentListView(APIView):
    """POST (Admin): a new department in a school {name}."""

    permission_classes = [IsAdmin]

    def post(self, request, unit_id):
        unit = OrgUnit.objects.filter(id=unit_id).first()
        if unit is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if unit.kind != OrgUnit.Kind.SCHOOL:
            return _error("Only a school has departments.")
        name = _name(request.data.get("name"))
        if not name:
            return _error("Type the department's name.")
        last = unit.departments.order_by("-sort_order").first()
        try:
            Department.objects.create(unit=unit, name=name, sort_order=(last.sort_order + 1) if last else 0)
        except IntegrityError:
            return _error(f"\"{name}\" is already in {unit.name}.")
        _audit(request, f"Added department \"{name}\" to {unit.name}")
        return Response(org_lists(), status=status.HTTP_201_CREATED)


class DepartmentDetailView(APIView):
    """PATCH (Admin): rename, hide or show a department."""

    permission_classes = [IsAdmin]

    def patch(self, request, department_id):
        dept = Department.objects.select_related("unit").filter(id=department_id).first()
        if dept is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        changes = []
        if "name" in request.data:
            name = _name(request.data["name"])
            if not name:
                return _error("The name can't be empty.")
            if dept.unit.departments.exclude(id=dept.id).filter(name=name).exists():
                return _error(f"\"{name}\" is already in {dept.unit.name}.")
            if name != dept.name:
                changes.append(f"renamed \"{dept.name}\" to \"{name}\" ({dept.unit.name})")
                dept.name = name
        if "is_active" in request.data:
            active = bool(request.data["is_active"])
            if active != dept.is_active:
                changes.append(f"{'showed' if active else 'hid'} \"{dept.name}\" ({dept.unit.name})")
                dept.is_active = active
        dept.save()
        if changes:
            _audit(request, "; ".join(changes))
        return Response(org_lists())
