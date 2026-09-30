import time

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import gateway
from .models import FakeLink

ACTIONS = {
    "pay_upi": {"method": "upi"},
    "pay_card": {"method": "card"},
    "pay_duplicate_webhooks": {"method": "upi", "deliver_times": 3},
    "pay_lost_webhooks": {"method": "upi", "send_webhooks": False},
    "pay_short": {"method": "upi", "short_by": 100},
}


def _require_sandbox():
    if settings.PAYMENT_GATEWAY != "fake":
        raise Http404("Sandbox gateway is disabled")


def _expire_if_due(link: FakeLink) -> None:
    if link.status == "created" and link.expire_by < time.time():
        gateway.expire_link(link)
        link.refresh_from_db()


def index(request):
    """Open links, newest first: a desktop shortcut for demos (no phone needed)."""
    _require_sandbox()
    links = list(FakeLink.objects.order_by("-created_at")[:30])
    for link in links:
        _expire_if_due(link)
    return render(request, "sandbox/index.html", {"links": links})


@require_http_methods(["GET", "POST"])
def pay(request, link_id):
    _require_sandbox()
    link = get_object_or_404(FakeLink, pk=link_id)
    _expire_if_due(link)

    if request.method == "POST" and link.status == "created":
        action = request.POST.get("action", "")
        if action == "fail":
            gateway.fail_link_payment(link)
            return redirect(f"{request.path}?result=failed")
        if action == "expire":
            gateway.expire_link(link)
            return redirect(request.path)
        if action in ACTIONS:
            options = dict(ACTIONS[action])
            short_by = options.pop("short_by", 0)
            gateway.pay_link(link, amount=link.amount - short_by, **options)
            return redirect(f"{request.path}?result=paid&scenario={action}")

    link.refresh_from_db()
    return render(
        request,
        "sandbox/pay.html",
        {
            "link": link,
            "amount_rupees": f"{link.amount / 100:,.2f}",
            "payments": link.payments.order_by("-created_at"),
            "result": request.GET.get("result"),
            "scenario": request.GET.get("scenario"),
        },
    )
