"""Demo data: 3 centres, staff users, services, customers and sample invoices.

python manage.py seed            # idempotent; safe to run twice
Logins: <centre code lowercased>_desk / <code>_manager, password "centrepay123"

The `admin` superuser gets the demo password only when DEBUG is on. On a public
deployment set SEED_ADMIN_PASSWORD, or no superuser is created (a well-known admin
password on a public URL would hand out the whole Django Admin).
"""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Centre, Staff
from apps.billing import services as billing
from apps.billing.models import Invoice, Service
from apps.customers.models import Customer

DEMO_PASSWORD = "centrepay123"  # noqa: S105  (demo data only)

CENTRES = [
    ("Indiranagar Physio & Wellness", "BLR1", "Bengaluru"),
    ("Koramangala Physio & Wellness", "BLR2", "Bengaluru"),
    ("Bandra Physio & Wellness", "MUM1", "Mumbai"),
]

# (name, price in rupees, GST bps). Physiotherapy is healthcare (exempt); wellness services are 18%.
SERVICES = [
    ("Physiotherapy consultation", 800, 0),
    ("Physiotherapy session (45 min)", 1200, 0),
    ("Sports massage (60 min)", 2000, 1800),
    ("Dry needling", 1500, 0),
    ("Yoga therapy class", 600, 1800),
]

CUSTOMERS = [
    ("Aarav Sharma", "+919812345601"),
    ("Priya Nair", "+919812345602"),
    ("Rohan Mehta", "+919812345603"),
    ("Ananya Iyer", "+919812345604"),
]


class Command(BaseCommand):
    help = "Create demo centres, staff, services, customers and invoices."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        admin_password = os.environ.get("SEED_ADMIN_PASSWORD") or (
            DEMO_PASSWORD if settings.DEBUG else None
        )
        if admin_password and not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", admin_password)
        elif not admin_password:
            self.stdout.write(
                "Skipping the admin superuser (set SEED_ADMIN_PASSWORD to create it)."
            )

        for name, code, city in CENTRES:
            centre, _ = Centre.objects.get_or_create(
                code=code, defaults={"name": name, "city": city}
            )

            staff_by_role = {}
            for role in Staff.Role:
                username = (
                    f"{code.lower()}_{'desk' if role == Staff.Role.FRONT_DESK else 'manager'}"
                )
                user, created = User.objects.get_or_create(
                    username=username, defaults={"first_name": role.label, "last_name": code}
                )
                if created:
                    user.set_password(DEMO_PASSWORD)
                    user.save()
                staff_by_role[role], _ = Staff.objects.get_or_create(
                    user=user, defaults={"centre": centre, "role": role}
                )

            services = [
                Service.objects.get_or_create(
                    centre=centre,
                    name=svc_name,
                    defaults={"price_paise": rupees * 100, "gst_rate_bps": gst},
                )[0]
                for svc_name, rupees, gst in SERVICES
            ]
            customers = [
                Customer.objects.get_or_create(
                    centre=centre, phone=phone, defaults={"name": cname}
                )[0]
                for cname, phone in CUSTOMERS
            ]

            if Invoice.objects.filter(centre=centre).exists():
                continue
            desk = staff_by_role[Staff.Role.FRONT_DESK]
            billing.create_draft(
                staff=desk,
                customer=customers[0],
                lines=[billing.LineInput(services[0], 1), billing.LineInput(services[1], 2)],
            )
            issued = billing.create_draft(
                staff=desk,
                customer=customers[1],
                lines=[billing.LineInput(services[2], 1), billing.LineInput(services[4], 4)],
                discount_paise=20_000,
            )
            billing.issue_invoice(issued.pk, staff=desk)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(CENTRES)} centres. Log in as e.g. blr1_desk / {DEMO_PASSWORD}"
            )
        )
