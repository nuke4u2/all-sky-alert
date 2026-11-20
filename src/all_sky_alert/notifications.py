"""Delivery adapters. OAuth is optional and never imported by detectors."""
from __future__ import annotations

import base64
import os
from email.message import EmailMessage
from pathlib import Path

import httpx

from .aircraft_display import aircraft_summary
from .presentation import local_time

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class SafeDeliveryError(Exception):
    """Failure known to precede acceptance; safe to retry."""


class UncertainDeliveryError(Exception):
    """Remote acceptance may have happened; do not retry automatically."""


def private_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    # Exclusive owner permissions on POSIX; Windows users set a private directory ACL.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def authorize(client_secret: Path, token: Path):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    credentials = flow.run_local_server(host="localhost", port=0, access_type="offline", prompt="consent")
    private_write(token, credentials.to_json())


def render_message(alert: dict, recipient: str, timezone: str = "UTC") -> EmailMessage:
    if alert.get('kind') == 'digest':
        from .digests import render_digest
        return render_digest(alert,recipient)
    prediction = alert["prediction"]
    event, assessment = prediction["candidate"], prediction["assessment"]
    message = EmailMessage()
    message["To"] = recipient
    # Gmail supplies the authenticated sender; no Gmail read/profile scope is needed.
    plane = aircraft_summary(event.get("metadata", {})) if event.get("detector") == "adsb" else None
    title = f"{plane['airline']} · {plane['type_name']} · {plane['origin']} → {plane['destination']} · {plane['identity']}" if plane else event["title"]
    message["Subject"] = f"All Sky Alert · {alert['stage']} · {title}"
    message["Message-ID"] = f"<{alert['id'].replace(':', '.')}@all-sky-alert.local>"
    quality = "unknown" if assessment["quality"] is None else f"{assessment['quality']}/100"
    text = ([f"Airline: {plane['airline']}", f"Plane type: {plane['type_name']}", f"Origin: {plane['origin']}", f"Destination: {plane['destination']}",
             f"Flight: {plane['identity']}", f"Reported ICAO type: {plane['type_code'] or 'unknown'}", ""] if plane else [])
    text += [event["title"], f"Observer timezone: {timezone}",
            f"Start: {local_time(event['start'], timezone)}", f"Peak: {local_time(event['peak'], timezone)}"]
    if event.get("end"):
        text.append(f"End: {local_time(event['end'], timezone)}")
    if event.get("detector") == "lunar_occultations":
        metadata = event["metadata"]
        text += ["", "Nominal Moon–planet occultation contacts:"]
        for contact in metadata["contacts"]:
            text.append(f"{contact['label']}: {local_time(contact['time'], timezone, seconds=True)}; "
                        f"planet elevation {contact['altitude_deg']}°; "
                        f"local viewing limits {'pass' if contact['observable'] else 'fail'}")
        text += [metadata["contact_definition"],
                 "Displayed seconds support planning; they are not actual prediction accuracy.", ""]
    text += [f"Intrinsic interest: {event['interest']}/100; observing quality: {quality}",
            f"Occurrence confidence: {event['occurrence_confidence']}; assessment confidence: {assessment['confidence']}",
            f"Atmosphere: {assessment['conditions']['status']}", "", *event["reasons"], *assessment["reasons"], "", "Sources:"]
    text += [f"{s['name']}: {s['url']}" for s in event["sources"]]
    if plane:
        text += [plane['type_method']]
        if plane['type_source_name']:
            text += [f"{plane['type_source_name']}: {plane['type_source_url']}"]
        if plane['route_source_name']:
            text += [f"Origin/destination source: {plane['route_source_name']}"]
        if plane['airline_source_name']:
            text += [f"Airline source: {plane['airline_source_name']}"]
    safety = event.get("metadata", {}).get("safety")
    if safety:
        text += ["", "SOLAR VIEWING SAFETY", safety["text"], safety["url"]]
    source = assessment["conditions"].get("source")
    if source:
        text.append(f"{source['name']}: {source['url']} (CC BY 4.0)")
    for extra in assessment["conditions"].get("additional_sources", []):
        text.append(f"{extra['name']}: {extra['url']} — {extra['method']}")
    seeing = assessment["conditions"].get("metrics", {}).get("seeing_index")
    if seeing:
        text += ["", f"Canadian seeing: {seeing['value']} (0 cloud masked; 1 very bad – 5 excellent)",
                 f"Valid: {local_time(seeing['valid_at'], timezone)}; model run: {local_time(seeing.get('issued_at'), timezone)}; {seeing.get('status', 'fresh')}",
                 "ECCC data licence: https://eccc-msc.github.io/open-data/licence/readme_en/"]
        if seeing.get('retrieved_at'):
            text.append(f"Seeing retrieved: {local_time(seeing['retrieved_at'], timezone)}")
    message.set_content("\n".join(text))
    return message


class GmailAdapter:
    name = "gmail"

    def __init__(self, token: Path, recipient: str, client: httpx.Client, timezone: str = "UTC"):
        self.token, self.recipient, self.client = token, recipient, client
        self.timezone = timezone

    def send(self, alert: dict) -> str:
        try:
            if not self.recipient or not self.token.is_file():
                raise ValueError("Gmail configuration missing")
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            credentials = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            if not credentials.valid:
                request = Request()
                credentials.refresh(lambda *a, **kw: request(*a, **{**kw, "timeout": 20}))
                private_write(self.token, credentials.to_json())
            raw = base64.urlsafe_b64encode(render_message(alert, self.recipient, self.timezone).as_bytes()).decode()
        except Exception:
            raise SafeDeliveryError("Gmail setup or token refresh failed") from None
        try:
            response = self.client.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {credentials.token}"}, json={"raw": raw})
        except httpx.RequestError:
            raise UncertainDeliveryError("Gmail request interrupted; delivery may have succeeded") from None
        if response.status_code >= 500:
            raise UncertainDeliveryError("Gmail server failure; delivery outcome unknown")
        if not response.is_success:
            raise SafeDeliveryError(f"Gmail rejected send: HTTP {response.status_code}")
        try:
            return str(response.json()["id"])
        except (ValueError, KeyError):
            raise UncertainDeliveryError("Gmail accepted request but returned no message ID") from None
