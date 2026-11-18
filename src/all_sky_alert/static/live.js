"use strict";
// Focused, read-only refresh. All external/provider text is rendered as text.
async function refreshAircraft() {
  const root = document.getElementById("live-aircraft");
  if (!root) return;
  const status = document.getElementById("live-status");
  const count = document.getElementById("live-count");
  const updateCount = () => {
    if (count) count.textContent = "· " + root.querySelectorAll("article").length + " fresh";
  };
  const add = (parent, tag, value) => {
    const node = document.createElement(tag);
    node.textContent = value;
    parent.appendChild(node);
    return node;
  };
  try {
    const sort = new URLSearchParams(window.location.search).get("sort") || "time_asc";
    const response = await fetch("/api/live?" + new URLSearchParams({sort}), {cache: "no-store", signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error("Unavailable");
    const data = await response.json();
    const received = performance.now();
    root.replaceChildren();
    for (const aircraft of data.aircraft) {
      const card = add(root, "article", "");
      card.className = "card aircraft-card";
      const remaining = Date.parse(aircraft.valid_until) - Date.parse(data.checked_at);
      setTimeout(() => {card.remove(); updateCount();}, Math.max(0, remaining - (performance.now() - received)));
      const summary = aircraft.aircraft_summary;
      add(card, "p", "Aircraft · " + aircraft.track_state.toLowerCase()).className = "eyebrow";
      add(card, "p", summary.airline).className = "aircraft-airline";
      const heading = add(card, "h3", "");heading.className = "aircraft-type";
      const link = add(heading, "a", summary.type_name);
      if (/^[a-f0-9]{32}$/.test(aircraft.id)) link.href = "/events/" + aircraft.id;
      const route = add(card, "div", "");route.className = "aircraft-route";
      for (const [label, value] of [["Origin", summary.origin], ["Destination", summary.destination]]) {
        const endpoint = add(route, "div", "");add(endpoint, "small", label);add(endpoint, "strong", value);
      }
      add(card, "p", summary.identity + (summary.type_code ? " · ICAO type " + summary.type_code : "")).className = "muted";
      // Prose is an evaluation snapshot; it may contain independently expired route metadata.
      if (summary.route_source_name) add(card, "p", "Route source: " + summary.route_source_name).className = "muted";
      const g = aircraft.geometry;
      add(card, "p", (g.altitude_m / .3048).toLocaleString(undefined, {maximumFractionDigits: 0}) + " ft · " + g.from_direction + (g.toward_direction ? " → " + g.toward_direction : ""));
      add(card, "p", g.altitude_deg.toFixed(1) + "° elevation · " + g.range_km.toFixed(1) + " km range · interest " + aircraft.interest + "/100");
      if (g.closest_in_seconds !== null) add(card, "p", "Projected closest approach " + g.closest_in_seconds + " s after evaluation; constant-motion estimate.");
      add(card, "p", "Position " + aircraft.position_age_seconds + " s old · visual detection not confirmed");
      const sources = add(card, "details", "");add(sources, "summary", "Aircraft metadata sources");
      add(sources, "p", summary.type_method + (summary.type_source_name ? " · " + summary.type_source_name : ""));
      if (summary.airline_source_name) add(sources, "p", "Airline source: " + summary.airline_source_name);
    }
    if (!data.aircraft.length) add(root, "p", "No fresh qualifying aircraft positions. Coverage or source gaps can also cause this.");
    updateCount();
    status.textContent = "Updates every 15 seconds. " + data.note;
  } catch (_) {
    root.replaceChildren(); // A stalled browser/provider cannot leave 'overhead now' visible.
    if (count) count.textContent = "· unavailable";
    status.textContent = "Live view unavailable. Recorded predictions remain in event history.";
  } finally {
    setTimeout(refreshAircraft, 15000);
  }
}
refreshAircraft();
