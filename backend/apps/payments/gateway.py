"""The only place that talks HTTP to Razorpay. Everything else uses `get_gateway()`,
which makes the gateway trivial to mock in tests (the `responses` library intercepts
the HTTP calls made here)."""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code

    @property
    def retryable(self) -> bool:
        """Network errors, timeouts, 429 and 5xx are worth retrying; other 4xx are not."""
        return self.status_code is None or self.status_code == 429 or self.status_code >= 500


class GatewayNotConfigured(GatewayError):
    pass


class RazorpayClient:
    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, timeout: float = 10.0):
        if not key_id or not key_secret:
            raise GatewayNotConfigured("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set")
        self.session = requests.Session()
        self.session.auth = (key_id, key_secret)
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.BASE_URL}{path}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            logger.warning("gateway.network_error method=%s path=%s error=%s", method, path, exc)
            raise GatewayError(f"Network error talking to Razorpay: {exc}") from exc

        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            message = err.get("description") or resp.text[:200]
            logger.warning(
                "gateway.error method=%s path=%s status=%s code=%s message=%s",
                method,
                path,
                resp.status_code,
                err.get("code"),
                message,
            )
            raise GatewayError(message, status_code=resp.status_code, code=err.get("code", ""))
        return resp.json()

    # --- Payment links ---

    def create_payment_link(
        self,
        *,
        amount_paise: int,
        reference_id: str,
        description: str,
        customer: dict,
        expire_by: int,
        notes: dict,
    ) -> dict:
        return self._request(
            "POST",
            "/payment_links",
            json={
                "amount": amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "reference_id": reference_id,
                "description": description[:2048],
                "customer": customer,
                "expire_by": expire_by,
                # Staff show the QR at the desk; the customer doesn't need an SMS/email.
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "notes": notes,
            },
        )

    def find_payment_link_by_reference(self, reference_id: str) -> dict | None:
        data = self._request("GET", "/payment_links", params={"reference_id": reference_id})
        links = data.get("payment_links", [])
        return links[0] if links else None

    def fetch_payment_link(self, link_id: str) -> dict:
        return self._request("GET", f"/payment_links/{link_id}")

    def cancel_payment_link(self, link_id: str) -> dict:
        return self._request("POST", f"/payment_links/{link_id}/cancel")

    # --- Payments ---

    def fetch_payment(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")

    def iter_payments(self, from_ts: int, to_ts: int, page_size: int = 100):
        """All payments created in [from_ts, to_ts] (unix seconds), paginated."""
        skip = 0
        while True:
            data = self._request(
                "GET",
                "/payments",
                params={"from": from_ts, "to": to_ts, "count": page_size, "skip": skip},
            )
            items = data.get("items", [])
            yield from items
            if len(items) < page_size:
                return
            skip += page_size

    # --- Refunds ---

    def create_refund(self, payment_id: str, *, amount_paise: int, receipt: str, notes: dict):
        return self._request(
            "POST",
            f"/payments/{payment_id}/refund",
            json={"amount": amount_paise, "speed": "normal", "receipt": receipt, "notes": notes},
        )

    def list_refunds(self, payment_id: str) -> list[dict]:
        return self._request("GET", f"/payments/{payment_id}/refunds", params={"count": 100}).get(
            "items", []
        )

    # --- Settlements ---

    def settlement_recon(self, year: int, month: int, day: int) -> list[dict]:
        """Every transaction settled on the given day, with its settlement id and fee."""
        items, skip = [], 0
        while True:
            data = self._request(
                "GET",
                "/settlements/recon/combined",
                params={"year": year, "month": month, "day": day, "count": 1000, "skip": skip},
            )
            page = data.get("items", [])
            items.extend(page)
            if len(page) < 1000:
                return items
            skip += 1000

    def fetch_settlement(self, settlement_id: str) -> dict:
        return self._request("GET", f"/settlements/{settlement_id}")


# Used by the sandbox gateway when no real webhook secret is configured.
SANDBOX_WEBHOOK_SECRET = "sandbox-webhook-secret"  # noqa: S105  (not a real secret)


def get_gateway():
    """The configured gateway: real Razorpay, or the local sandbox (PAYMENT_GATEWAY=fake)."""
    if settings.PAYMENT_GATEWAY == "fake":
        from apps.sandbox.gateway import FakeGateway

        return FakeGateway()
    return RazorpayClient(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)


def webhook_secret() -> str:
    if settings.RAZORPAY_WEBHOOK_SECRET:
        return settings.RAZORPAY_WEBHOOK_SECRET
    return SANDBOX_WEBHOOK_SECRET if settings.PAYMENT_GATEWAY == "fake" else ""
