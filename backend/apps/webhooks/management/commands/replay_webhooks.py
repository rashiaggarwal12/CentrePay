"""Re-run stored webhook events.

    python manage.py replay_webhooks --unprocessed            # everything not yet processed
    python manage.py replay_webhooks --id 42 --id 43 --force  # re-run specific events
    python manage.py replay_webhooks --unprocessed --sync     # run inline, not via Celery

Handlers are idempotent, so --force on an already-processed event is safe.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import process_event
from apps.webhooks.tasks import process_webhook_event


class Command(BaseCommand):
    help = "Replay stored Razorpay webhook events."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, action="append", dest="ids", default=[])
        parser.add_argument("--unprocessed", action="store_true")
        parser.add_argument("--force", action="store_true", help="Re-run even if processed")
        parser.add_argument("--sync", action="store_true", help="Process inline")

    def handle(self, *args, ids, unprocessed, force, sync, **options):
        if not ids and not unprocessed:
            raise CommandError("Pass --id <pk> (repeatable) and/or --unprocessed.")

        pks = set(ids)
        if unprocessed:
            pks |= set(
                WebhookEvent.objects.filter(processed_at__isnull=True).values_list("pk", flat=True)
            )

        for pk in sorted(pks):
            if sync:
                try:
                    outcome = process_event(pk, force=force)
                except Exception as exc:  # report and carry on with the rest
                    self.stderr.write(f"event {pk}: FAILED {type(exc).__name__}: {exc}")
                    continue
                self.stdout.write(f"event {pk}: {outcome}")
            else:
                process_webhook_event.delay(pk, force=force)
                self.stdout.write(f"event {pk}: queued")
