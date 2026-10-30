"""Incident alerts to a Discord (or Slack-compatible) incoming webhook, when ALERT_WEBHOOK_URL is set."""

from __future__ import annotations

import httpx

from hindsight.core.schemas import Incident


def alert_text(incident: Incident, base_url: str = "http://localhost:5173") -> str:
    culprit = next((r.service for r in incident.ranking if r.role == "culprit"), "unknown")
    lines = [f"**Incident #{incident.id}**: likely culprit **{culprit}**"]
    if incident.candidates:
        top = incident.candidates[0]
        lines.append(
            f"Top candidate change: `{top.commit[:7]}` {top.title} (correlation, not proof)"
        )
    lines.append(f"Details: {base_url}  (Incidents → #{incident.id})")
    return "\n".join(lines)


def send_alert(webhook: str, incident: Incident, client: httpx.Client | None = None) -> bool:
    if not webhook:
        return False
    owned = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.post(webhook, json={"content": alert_text(incident)[:1900]})
        return resp.status_code < 300
    except httpx.HTTPError:
        return False
    finally:
        if owned:
            client.close()
