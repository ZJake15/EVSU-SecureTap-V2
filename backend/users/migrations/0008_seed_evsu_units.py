"""EVSU's schools, their departments and the administrative offices, as listed
on www.evsu.edu.ph (Academics > Schools and Departments, and Key Officials and
Administrative/Supervisory Staff of the University), November 2026. An Admin
can add, rename or hide entries later in Settings > Schools, Offices &
Departments."""

from django.db import migrations

SCHOOLS = [
    ("School of Architecture and Allied Disciplines", ["Architecture Department", "Interior Design Department"]),
    ("School of Arts and Sciences", [
        "Chemistry Department", "Counseling and Human Services Department", "Mathematics and Statistics Department",
        "Languages and Literature Department", "Natural Sciences Department", "Economics Department",
    ]),
    ("School of Accountancy, Management and Entrepreneurship", [
        "Accountancy Department", "Entrepreneurship Department", "Hospitality and Tourism Management Department",
        "Marketing Department", "Office Administration Department",
    ]),
    ("School of Education", [
        "Science and Mathematics Education Department", "Technology and Livelihood Education Department",
        "Elementary Education Department", "Culture, Arts and Physical Education Department",
        "Field Study and Student Teaching Department",
    ]),
    ("School of Engineering", [
        "Chemical Engineering Department", "Civil Engineering Department", "Electrical Engineering Department",
        "Electronics Engineering Department", "Geodetic Engineering Department", "Mechanical Engineering Department",
        "Industrial Engineering Department", "Information Technology Department",
    ]),
    ("School of Technology", ["Industrial Technology Department", "Nutrition and Dietetics Department"]),
    ("Graduate School", []),
]

OFFICES = [
    ("Office of the University President", [
        "Office of the University President", "University and Board Secretary", "Legal Office",
        "Multi-Media Development Center", "Internal Audit Service", "Gender and Development Office",
        "Quality Assurance Office", "Institutional Planning and Development Office",
        "Information and Communications Technology Office", "Disaster Risk Reduction Management Office",
    ]),
    ("Academic Affairs", [
        "Office of the Vice President for Academic Affairs", "National Service Training Program and Greening Office",
        "Curriculum and Instructional Materials Development Office", "University Library", "University Registrar",
    ]),
    ("Research, Innovation and Extension Services", [
        "Office of the Vice President for Research, Innovation and Extension Services", "Research Office",
        "Extension Services Office", "Innovation Office",
    ]),
    ("Administration, Finance and Auxiliary Services", [
        "Office of the Vice President for Administration, Finance and Auxiliary Services",
        "Human Resource Management and Development Office", "Records Management Office",
        "Property Management Office", "Procurement Management Office", "Contract Management Office",
        "Security Office", "General Services and Occupational Safety Office", "Motorpool Maintenance Office",
        "Audio, Visual, Lighting and Technical Services", "Pollution Control Office", "Cashier's Office",
        "Budget Office", "Accounting Office", "Billing and Monitoring Office", "Pre-Audit Office",
        "Auxiliary Services Office and Income Generating Projects", "Medical Clinic", "Dental Clinic",
        "Executive House / Dormitory", "Welfare and Wellness Office",
    ]),
    ("Students, Internationalization and External Affairs", [
        "Office of the Vice President for Students, Internationalization and External Affairs",
        "Culture and the Arts Office", "Sports Development Office", "Alumni Relations and Affairs Office",
        "External Affairs Office", "Internationalization Affairs Office", "Student Affairs Office",
        "Student Publication Office", "Guidance and Counseling Office", "Admission and Testing Office",
        "Student Scholarship Office",
    ]),
]


def seed(apps, schema_editor):
    OrgUnit = apps.get_model("users", "OrgUnit")
    Department = apps.get_model("users", "Department")
    for order, (name, departments) in enumerate(SCHOOLS):
        unit, _ = OrgUnit.objects.get_or_create(name=name, defaults={"kind": "school", "group": "Schools",
                                                                      "sort_order": order})
        for dept_order, dept in enumerate(departments):
            Department.objects.get_or_create(unit=unit, name=dept, defaults={"sort_order": dept_order})
    order = 0
    for group, offices in OFFICES:
        for name in offices:
            OrgUnit.objects.get_or_create(name=name, defaults={"kind": "office", "group": group, "sort_order": order})
            order += 1


def unseed(apps, schema_editor):
    OrgUnit = apps.get_model("users", "OrgUnit")
    names = [name for name, _ in SCHOOLS] + [name for _, offices in OFFICES for name in offices]
    OrgUnit.objects.filter(name__in=names, people__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_org_units"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
