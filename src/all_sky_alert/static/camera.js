"use strict";
(() => {
  const root = document.getElementById('camera-controls');
  const byId = id => document.getElementById(id);
  const image = byId('camera-preview');
  const result = byId('camera-result');
  let state = null, pending = null, focusInitialized = false;
  const formatTime = value => value ? new Date(value).toLocaleString(undefined, {timeZone: root.dataset.timezone}) : 'Unknown';
  function interval() {
    const seconds = Number(byId('capture-interval').value) * Number(byId('interval-unit').value);
    if (!Number.isFinite(seconds) || seconds < 10 || seconds > 604800) throw Error('Use an interval between 10 seconds and 7 days.');
    return seconds;
  }
  function hidePreview() { image.removeAttribute('src'); image.hidden = true; }
  image.addEventListener('error', () => { hidePreview(); byId('preview-message').textContent = 'Preview unavailable. Check camera status and retry Show live view.'; });
  byId('preview-start').addEventListener('click', () => { image.src = '/camera/stream'; image.hidden = false; byId('preview-message').textContent = ''; });
  byId('preview-stop').addEventListener('click', hidePreview);
  window.addEventListener('pagehide', hidePreview);
  async function command(payload, button) {
    button.disabled = true;
    try {
      const response = await fetch('/camera/command', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
        body: JSON.stringify(payload), signal: AbortSignal.timeout(15000)});
      const data = await response.json();
      if (!response.ok) throw Error(data.detail || 'Camera command failed');
      if (data.revision) root.dataset.revision = data.revision;
      if (data.id) pending = data.id;
      result.textContent = data.message || 'Camera command queued. Waiting for completion…';
      await refresh();
    } catch (error) { result.textContent = error.message + ' Check status before repeating a capture.'; }
    finally { button.disabled = false; updateControls(); }
  }
  function updateControls() {
    if (!state) return;
    const available = ['ready', 'capturing'].includes(state.state);
    const cap = state.capabilities;
    for (const id of ['capture', 'timelapse-start']) byId(id).disabled = !available;
    byId('preview-start').disabled = !available || !state.preview_enabled;
    byId('focus-mode').disabled = !available || !cap.focus_modes.length;
    byId('focus-apply').disabled = !available || !cap.focus_modes.length;
    byId('autofocus').disabled = !available || !cap.autofocus_trigger;
    byId('lens-position').disabled = !available || !cap.lens_limits || byId('focus-mode').value !== 'Manual';
  }
  byId('focus-mode').addEventListener('change', updateControls);
  byId('focus-apply').addEventListener('click', event => {
    const payload = {action: 'focus', mode: byId('focus-mode').value};
    if (payload.mode === 'Manual' && !byId('lens-position').disabled && byId('lens-position').value !== '') payload.lens_position = Number(byId('lens-position').value);
    command(payload, event.target);
  });
  byId('autofocus').addEventListener('click', event => command({action: 'focus', mode: 'Auto', trigger: true}, event.target));
  byId('capture').addEventListener('click', event => command({action: 'capture'}, event.target));
  for (const [id, action] of [['timelapse-start', 'start'], ['timelapse-stop', 'stop'], ['timelapse-save', 'save']]) {
    byId(id).addEventListener('click', event => {
      try {
        const seconds = action === 'stop' ? state.interval_seconds : interval();
        command(action === 'save' ? {action: 'save_timelapse', enabled: byId('startup-timelapse').checked, interval_seconds: seconds, revision: root.dataset.revision}
          : {action: 'timelapse', running: action === 'start', interval_seconds: seconds}, event.target);
      } catch (error) { result.textContent = error.message; }
    });
  }
  async function refresh() {
    try {
      const response = await fetch('/camera/status', {signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw Error('Camera status unavailable');
      state = await response.json();
      const rows = [['State', state.state], ['Detected', state.detected === null ? 'Unknown' : state.detected ? 'Yes' : 'No / unavailable'],
        ['Model', state.model || 'Unknown'], ['Still resolution', state.sensor_resolution ? state.sensor_resolution.join(' × ') : 'Unknown'],
        ['Preview', `${state.preview_state} (${state.preview_width} × ${state.preview_height}, ${state.preview_fps} fps)`],
        ['Focus mode', state.focus_mode || 'Unsupported / unknown'], ['Lens position', state.lens_position ?? 'Unknown'],
        ['Autofocus state', ['Idle', 'Scanning', 'Focused', 'Failed'][state.autofocus_state] || 'Unknown'],
        ['Timelapse', state.timelapse_running ? 'Running' : 'Stopped'], ['Interval', `${state.interval_seconds} seconds`],
        ['Last capture', formatTime(state.last_capture_at)], ['Latest filename', state.last_filename || 'None'],
        ['Next capture', formatTime(state.next_capture_at)], ['Stills directory', state.capture_directory], ['Timelapse directory', state.timelapse_directory]];
      const list = byId('camera-status'); list.replaceChildren();
      for (const [label, value] of rows) { const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = label; dd.textContent = value; list.append(dt, dd); }
      byId('camera-error').textContent = state.error || (state.enabled ? '' : 'Camera disabled. Enable it in Settings, then restart the service.');
      byId('latest-image').hidden = !state.last_filename;
      const cap = state.capabilities;
      if (!focusInitialized && cap.focus_modes.length) {
        const select = byId('focus-mode'); select.replaceChildren();
        for (const mode of cap.focus_modes) { const option = document.createElement('option'); option.value = mode; option.textContent = mode; select.append(option); }
        select.value = state.focus_mode || cap.focus_modes[0];
        if (cap.lens_limits) { const input = byId('lens-position'); input.min = cap.lens_limits[0]; input.max = cap.lens_limits[1]; input.value = state.lens_position ?? cap.lens_limits[2]; }
        focusInitialized = true;
      }
      byId('focus-support').textContent = cap.focus_modes.length ? (cap.lens_limits ? `Lens limits from camera: ${cap.lens_limits[0]}–${cap.lens_limits[1]} dioptres. Calibration is approximate.` : 'Manual lens control unsupported.') : 'Autofocus controls unsupported / camera unavailable.';
      if (pending) { const job = state.jobs.find(job => job.id === pending); if (job && ['done', 'failed', 'canceled'].includes(job.status)) { result.textContent = job.error || (job.action === 'capture' ? 'Image captured: ' + state.last_filename : 'Focus controls applied. Check autofocus state for the result.'); pending = null; } }
      updateControls();
    } catch (error) { byId('camera-error').textContent = error.message; }
  }
  async function poll() { await refresh(); window.setTimeout(poll, 2000); }
  poll();
})();
