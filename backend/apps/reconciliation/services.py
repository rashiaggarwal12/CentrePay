import logging

from django.db import IntegrityError, transaction

from .models import ReconciliationIssue

logger = logging.getLogger(__name__)

Kind = ReconciliationIssue.Kind


def raise_issue(
    kind: str,
    gateway_ref: str,
    *,
    invoice=None,
    local_ref: str = "",
    expected_paise: int | None = None,
    actual_paise: int | None = None,
    details: dict | None = None,
    run=None,
) -> ReconciliationIssue:
    """Open an issue, or return the already-open one for the same (kind, gateway_ref).

    Idempotent, so it is safe to call from retried tasks and repeated reconciliation runs.
    """
    existing = ReconciliationIssue.objects.filter(
        kind=kind, gateway_ref=gateway_ref, status=ReconciliationIssue.Status.OPEN
    ).first()
    if existing:
        return existing
    try:
        with transaction.atomic():
            issue = ReconciliationIssue.objects.create(
                kind=kind,
                gateway_ref=gateway_ref,
                invoice=invoice,
                local_ref=local_ref,
                expected_paise=expected_paise,
                actual_paise=actual_paise,
                details=details or {},
                run=run,
            )
    except IntegrityError:
        # Raced with another worker raising the same issue.
        return ReconciliationIssue.objects.get(
            kind=kind, gateway_ref=gateway_ref, status=ReconciliationIssue.Status.OPEN
        )
    logger.warning(
        "reconciliation.issue_raised kind=%s gateway_ref=%s invoice=%s",
        kind,
        gateway_ref,
        getattr(invoice, "pk", None),
    )
    return issue
