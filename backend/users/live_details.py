"""Who someone is, in parts, for the gate monitor's live log and the
dashboard's Live Monitoring page: STUDENT or STAFF, their school or office,
their department, and their role (staff: type and job title; students: year
level). Settings > Live Log Details switches each part on or off; a part
that's off - or that they don't have - is simply left out.

Someone registered before schools and offices were a list has only their old
free text, shown as "previous: ..." in place of the school or office."""

from configuration import store as system_settings

# (part, its switch)
PARTS = (
    ("kind", "livelog_kind"),
    ("unit", "livelog_unit"),
    ("department", "livelog_department"),
    ("role", "livelog_role"),
)


def all_details(person):
    """Every part, whatever the switches say."""
    if person is None:
        return {}
    staff = person.role == person.Role.STAFF
    if person.org_unit_id:
        unit = person.org_unit.name
    else:
        unit = f"previous: {person.department_or_course}" if person.department_or_course else ""
    if staff:
        role = " · ".join(part for part in (
            person.get_staff_type_display() if person.staff_type else "", person.job_title) if part)
    else:
        role = person.get_year_level_display() if person.year_level else ""
    return {
        "kind": "Staff" if staff else "Student",
        "unit": unit,
        "department": person.department.name if person.department_id else "",
        "role": role,
    }


def shown(person):
    """The parts switched on in Settings that this person has, e.g.
    {"kind": "Student", "unit": "School of Engineering", "role": "3rd year"}."""
    details = all_details(person)
    return {part: details[part] for part, switch in PARTS if details.get(part) and system_settings.get(switch)}
