"use strict";
// Focused, read-only refresh. All external/provider text is rendered as text.
async function refreshAircraft() {
  const root = document.getElementById("live-aircraft");
  if (!root) return;
  const status = document.getElementById("live-status");
  const add = (parent, tag, value) => {
    const node = document.createElement(tag);
    node.textContent = value;
    parent.appendChild(node);
    return node;
  };
  try {
    const sort = new URLSearchParams(window.location.search).get("sort") || "time_desc";
    const response = await fetch("/api/live?" + new URLSearchParams({sort}), {cache: "no-store", signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error("Unavailable");
    const data = await response.json();
    const received = performance.now();
    root.replaceChildren();
    for (const aircraft of data.aircraft) {
      const card = add(root, "article", "");
      card.className = "card";
      const remaining = Date.parse(aircraft.valid_until) - Date.parse(data.checked_at);
      setTimeout(() => card.remove(), Math.max(0, remaining - (performance.now() - received)));
      const heading = add(card, "h3", "");
      const link = add(heading, "a", aircraft.title);
      if (/^[a-f0-9]{32}$/.test(aircraft.id)) link.href = "/events/" + aircraft.id;
      add(card, "p", aircraft.prose);
      const g = aircraft.geometry;
      add(card, "p", g.altitude_deg.toFixed(1) + "° elevation · " + g.range_km.toFixed(1) + " km range · interest " + aircraft.interest + "/100");
      if (g.closest_in_seconds !== null) add(card, "p", "Projected closest approach " + g.closest_in_seconds + " s after evaluation; constant-motion estimate.");
      add(card, "p", "Position " + aircraft.position_age_seconds + " s old · visual detection not confirmed");
    }
    if (!data.aircraft.length) add(root, "p", "No fresh qualifying aircraft positions. Coverage or source gaps can also cause this.");
    status.textContent = "Updates every 15 seconds. " + data.note;
  } catch (_) {
    root.replaceChildren(); // A stalled browser/provider cannot leave 'overhead now' visible.
    status.textContent = "Live view unavailable. Recorded predictions remain in event history.";
  } finally {
    setTimeout(refreshAircraft, 15000);
  }
}
refreshAircraft();
