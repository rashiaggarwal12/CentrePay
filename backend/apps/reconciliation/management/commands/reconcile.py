"""python manage.py reconcile [--date YYYY-MM-DD]   (default: yesterday, IST)"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.reconciliation.engine import run_reconciliation


class Command(BaseCommand):
    help = "Compare gateway payments/refunds/settlements with local records for one day."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="IST calendar day, YYYY-MM-DD (default: yesterday)")

    def handle(self, *args, **options):
        raw = options["date"]
        try:
            day = (
                datetime.date.fromisoformat(raw)
                if raw
                else timezone.localdate() - datetime.timedelta(days=1)
            )
        except ValueError as exc:
            raise CommandError("--date must be YYYY-MM-DD") from exc
        run = run_reconciliation(day)
        self.stdout.write(f"Reconciliation {day}: {run.status}")
        for key, value in run.summary.items():
            self.stdout.write(f"  {key}: {value}")
        if run.status != run.Status.SUCCEEDED:
            raise CommandError("Reconciliation did not complete; see summary above.")
