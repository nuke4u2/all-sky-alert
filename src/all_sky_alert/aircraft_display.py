"""I/O-free aircraft presentation, shared by the web UI and mail renderer."""
from .domain import parse


TYPE_REFERENCE = "https://www.faa.gov/documentLibrary/media/Order/2019-10-10_Order_JO_7360.1E_Aircraft_Type_Designators_FINAL.pdf"
# Deliberately bounded reference, not a prefix heuristic. Group variants into
# base models; retain the reported designator separately. Unknown codes stay unknown.
# FAA JO 7360.1E (2019-10-10), Appendix A; US government reference data.
_FAMILIES = (
    ("Airbus", "A300", "A30B A306"), ("Airbus", "A310", "A310"),
    ("Airbus", "A318", "A318"), ("Airbus", "A319", "A319 A19N"),
    ("Airbus", "A320", "A320 A20N"), ("Airbus", "A321", "A321 A21N"),
    ("Airbus", "A330", "A332 A333 A338 A339"),
    ("Airbus", "A340", "A342 A343 A345 A346"),
    ("Airbus", "A350", "A359 A35K"), ("Airbus", "A380", "A388"),
    ("Boeing", "717", "B712"), ("Boeing", "727", "B721 B722"),
    ("Boeing", "737", "B731 B732 B733 B734 B735 B736 B737 B738 B739 B37M B38M B39M B3XM"),
    ("Boeing", "747", "B741 B742 B743 B744 B748 B74S"),
    ("Boeing", "757", "B752 B753"), ("Boeing", "767", "B762 B763 B764"),
    ("Boeing", "777", "B772 B773 B77L B77W"),
    ("Boeing", "787", "B788 B789 B78X"),
    ("Embraer", "E170", "E170"), ("Embraer", "E175", "E75L E75S"),
    ("Embraer", "E190", "E190 E290"), ("Embraer", "E195", "E195 E295"),
    ("Cessna", "172", "C172"), ("Cessna", "182", "C182"),
    ("Cessna", "208", "C208"),
)
_TYPES = {code: (maker, model) for maker, model, codes in _FAMILIES for code in codes.split()}


def aircraft_summary(metadata, now=None):
    """Format prepared metadata only; optional now ages live enrichment independently.

    Without now, show the recorded enrichment as it was at event evaluation.
    Never derive a route from a callsign, heading, aircraft address or type.
    """
    enriched = {key: value.strip() if isinstance(value, str) else value
                for key, value in (metadata.get("enrichment") or {}).items()}
    deadline = enriched.get("usable_until") or enriched.get("valid_until")
    if now is not None and deadline and now > parse(deadline):
        enriched = {}
    code = metadata.get("type_code")
    maker, model = enriched.get("manufacturer"), enriched.get("base_model")
    name = enriched.get("type_name")
    source_name, source_url = enriched.get("source_name"), enriched.get("source_url")
    method = "Sourced aircraft metadata"
    if maker and model:
        name = f"{maker} {model}"
    elif not name:
        maker, model = _TYPES.get((code or "").strip().upper(), (None, None))
        name = f"{maker} {model}" if maker else "Aircraft type unknown"
        source_name = "FAA JO 7360.1E · 2019 reference" if maker else None
        source_url = TYPE_REFERENCE if maker else None
        method = "Base model family decoded from reported ICAO type; variants and license-built manufacturers may differ" if maker else "No matching type reference"
    return {
        "type_name": name, "manufacturer": maker, "base_model": model,
        "type_code": code, "type_source_name": source_name,
        "type_source_url": source_url, "type_method": method,
        "identity": metadata.get("callsign") or "Aircraft " + (metadata.get("icao24") or "unknown").upper(),
        "airline": enriched.get("airline") or "Airline unknown",
        "airline_source_name": enriched.get("source_name") if enriched.get("airline") else None,
        "origin": enriched.get("origin") or "Unknown origin",
        "destination": enriched.get("destination") or "Unknown destination",
        "route_source_name": enriched.get("source_name") if enriched.get("origin") and enriched.get("destination") else None,
    }
