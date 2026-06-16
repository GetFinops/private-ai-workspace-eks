"""Google Calendar integration for the M13 harness (first real provider).

The first REAL personal-information integration adopted on the harness (NOTICE:
"M13 Google Calendar integration"). It is a pure request *builder* — it never
opens a socket; the executor validates and sends through the hardened URL guard.
No SDK is vendored: requests are plain HTTPS against the public Google Calendar
REST API, so there is no copyleft/dependency footprint to review.

Credential model: per-tenant (per-user where configured) OAuth2 credentials are
resolved at request time from AWS Secrets Manager via IRSA, exactly like every
integration. The resolved secret provides a current OAuth2 access token under
``ACCESS_TOKEN``; it is sent as a bearer to googleapis.com. Refresh-token
exchange (minting a fresh access token via oauth2.googleapis.com) is a documented
follow-up — it is a second guarded outbound call and is intentionally out of this
first increment; until then the stored access token is used directly.

Security: ``permit_private_hosts`` is empty (unlike the dev fixture), so the full
URL guard applies — only the public ``www.googleapis.com`` host is reachable, and
private/loopback/metadata targets are refused.
"""

from __future__ import annotations

from urllib.parse import quote

from app.control_plane.integrations import OutboundRequest, UnknownOperation

_API_HOST = "www.googleapis.com"
_MAX_RESULTS_CAP = 250


def _bearer(creds: "dict | None") -> str:
    return (creds or {}).get("ACCESS_TOKEN", "")


class GoogleCalendarIntegration:
    """Read-only Google Calendar access (events list / single event)."""

    name = "google_calendar"
    requires_secret = True
    allowed_hosts = frozenset({_API_HOST})
    # Real integration: the full guard applies. NEVER permit private hosts here.
    permit_private_hosts = frozenset()

    def build_request(self, operation: str, params: dict, creds: "dict | None") -> OutboundRequest:
        headers = {
            "Authorization": f"Bearer {_bearer(creds)}",
            "Accept": "application/json",
        }
        if operation == "list_events":
            calendar_id = str(params.get("calendar_id", "primary"))
            try:
                max_results = int(params.get("max_results", 10))
            except (TypeError, ValueError):
                max_results = 10
            max_results = max(1, min(max_results, _MAX_RESULTS_CAP))
            path = f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
            query = f"maxResults={max_results}&singleEvents=true&orderBy=startTime"
            return OutboundRequest(
                method="GET",
                url=f"https://{_API_HOST}{path}?{query}",
                headers=headers,
            )
        if operation == "get_event":
            calendar_id = str(params.get("calendar_id", "primary"))
            event_id = params.get("event_id")
            if not event_id or not isinstance(event_id, str):
                raise UnknownOperation()  # missing required arg → treated as unsupported call
            path = (
                f"/calendar/v3/calendars/{quote(calendar_id, safe='')}"
                f"/events/{quote(event_id, safe='')}"
            )
            return OutboundRequest(method="GET", url=f"https://{_API_HOST}{path}", headers=headers)
        raise UnknownOperation()


def register(registry: dict) -> dict:
    """Add Google Calendar to a harness registry (in place) and return it."""
    registry[GoogleCalendarIntegration.name] = GoogleCalendarIntegration()
    return registry
