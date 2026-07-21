const POLL_MS = 2000;
let manualView = null;
let replayMode = false;
let _savedDelay = 0;
let _clockBase = null;

function _parseRemainingToSeconds(timeStr) {
  if (!timeStr) return null;
  const parts = timeStr.split(':').map(Number);
  if (parts.some(isNaN) || parts.length < 2) return null;
  return parts.length === 3
    ? parts[0] * 3600 + parts[1] * 60 + parts[2]
    : parts[0] * 60 + parts[1];
}

function _formatSeconds(totalSec) {
  const s = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function _tickClock() {
  if (!_clockBase || !_clockBase.extrapolating) return;
  const elapsed = (Date.now() - _clockBase.receivedAt) / 1000;
  const remaining = _clockBase.totalSeconds - elapsed;
  const el = document.getElementById('time-remaining');
  if (el) el.textContent = remaining > 0 ? _formatSeconds(remaining) : '0:00';
}

async function fetchState() {
  try {
    const resp = await fetch('/api/state');
    if (!resp.ok) return;
    const data = await resp.json();
    updateUI(data);
  } catch (e) {
    console.warn('state fetch failed', e);
  }
}

function updateUI(data) {
  const isActive = data.session && data.session.is_active;
  const targetView = manualView || (isActive ? 'live' : 'standings');
  // Both 'live' and 'replay' render view-live content
  document.getElementById('view-live').classList.toggle('active', targetView === 'live' || targetView === 'replay');
  document.getElementById('view-standings').classList.toggle('active', targetView === 'standings');
  document.getElementById('btn-live').classList.toggle('active', targetView === 'live');
  document.getElementById('btn-replay').classList.toggle('active', targetView === 'replay');
  document.getElementById('btn-standings').classList.toggle('active', targetView === 'standings');
  const _pill = document.getElementById('view-toggle-pill');
  if (_pill) {
    _pill.classList.remove('centre', 'right');
    if (targetView === 'replay') _pill.classList.add('centre');
    else if (targetView === 'standings') _pill.classList.add('right');
  }

  const isReplay = targetView === 'replay';
  document.getElementById('replay-bar').style.display = isReplay ? 'flex' : 'none';

  const rs = data.replay_status || 'idle';
  const autoLabel = document.getElementById('auto-label');
  if (autoLabel) {
    const rn = data.replay_session_name || '';
    if (!isReplay) {
      autoLabel.textContent = 'Switches automatically · Manual override above';
    } else if (rs === 'idle' || rs === 'loading') {
      autoLabel.textContent = 'Select a race to begin';
    } else if (rs === 'ready') {
      autoLabel.textContent = 'Press Play at lights out · Use slider to fine-tune sync';
    } else if (rs === 'playing') {
      autoLabel.textContent = `Replaying: ${rn}`;
    } else if (rs === 'paused') {
      autoLabel.textContent = `Paused · ${rn}`;
    }
  }

  document.getElementById('replay-idle-row').style.display =
    (isReplay && (rs === 'idle' || rs === 'loading')) ? 'flex' : 'none';
  document.getElementById('replay-ready-row').style.display =
    (isReplay && rs === 'ready') ? 'flex' : 'none';
  document.getElementById('replay-playback-row').style.display =
    (isReplay && (rs === 'playing' || rs === 'paused')) ? 'flex' : 'none';

  const pauseBtn = document.getElementById('btn-replay-pause');
  if (pauseBtn) pauseBtn.textContent = rs === 'paused' ? '▶ Resume' : '⏸ Pause';

  const replayPill = document.getElementById('session-replay-pill');
  if (replayPill) replayPill.style.display = data.replay_mode ? 'inline' : 'none';

  const newReplayMode = !!data.replay_mode;
  if (newReplayMode !== replayMode) {
    replayMode = newReplayMode;
    const slider = document.getElementById('delay-slider');
    const label = document.getElementById('delay-label');
    if (replayMode) {
      label.textContent = 'Sync Offset';
      slider.min = -30;
      slider.max = 30;
      slider.value = 0;
      document.getElementById('delay-value').textContent = '0';
    } else {
      label.textContent = 'LED Delay';
      slider.min = 0;
      slider.max = 90;
      slider.value = _savedDelay;
      document.getElementById('delay-value').textContent = _savedDelay;
    }
  }

  const color = data.flag_color || '#444';
  const status = (data.track_status || 'unknown').replace(/_/g, ' ').toUpperCase();
  const banner = document.getElementById('flag-banner');
  banner.style.borderLeftColor = color;
  banner.style.background = hexToRgba(color, 0.1);
  document.getElementById('flag-dot').style.background = color;
  document.getElementById('flag-label').textContent = status;
  const rcMsgs = data.race_control_messages || [];
  document.getElementById('flag-sub').textContent = rcMsgs.length ? rcMsgs[0].message : '';

  const s = data.session || {};
  document.getElementById('circuit-flag').textContent = s.country_flag || '';
  document.getElementById('circuit-name').textContent = s.name || '—';
  document.getElementById('circuit-venue').textContent = s.circuit || '';
  document.getElementById('session-type').textContent = s.session_type || '—';
  const _remaining = s.total_laps && s.current_lap ? s.total_laps - s.current_lap : null;
  document.getElementById('session-lap').textContent =
    _remaining !== null ? `${_remaining}/${s.total_laps} laps remaining` :
    s.current_lap ? `Lap ${s.current_lap}` : '';
  if (s.time_remaining) {
    const totalSeconds = _parseRemainingToSeconds(s.time_remaining);
    const receivedAt = s.time_remaining_at ? new Date(s.time_remaining_at).getTime() : Date.now();
    if (totalSeconds !== null) _clockBase = { totalSeconds, receivedAt, extrapolating: !!s.extrapolating };
  }
  if (!_clockBase || !_clockBase.extrapolating) {
    document.getElementById('time-remaining').textContent = s.time_remaining || '—';
  } else {
    _tickClock();
  }

  const w = data.weather || {};
  document.getElementById('air-temp').textContent = w.air_temp != null ? `${w.air_temp}°C` : '—';
  document.getElementById('track-temp').textContent = w.track_temp != null ? `${w.track_temp}°C` : '—';
  document.getElementById('humidity').textContent = w.humidity != null ? `${w.humidity}%` : '—';
  document.getElementById('wind').textContent = w.wind_speed != null ? `${w.wind_speed} km/h` : '—';
  document.getElementById('wind-dir').textContent = w.wind_direction || '';
  document.getElementById('rain').textContent = w.rain ? 'Yes' : 'No';
  document.getElementById('rain').style.color = w.rain ? '#4fc3f7' : '#aaa';

  _setDemoMode(!!data.demo_mode);
  _setLedEnabled(!!data.led_enabled);

  const feedEl = document.getElementById('feed-status');
  if (feedEl) {
    feedEl.textContent = data.feed_connected ? 'Connected' : 'Disconnected';
    feedEl.style.color = data.feed_connected ? '#00C853' : '#FF5252';
  }

  renderPositions(data.driver_positions || []);
  renderRCMessages(rcMsgs);

  const nr = data.next_race || {};
  document.getElementById('nr-flag').textContent = nr.country_flag || '';
  document.getElementById('nr-name').textContent = nr.name || '—';
  document.getElementById('nr-meta').textContent = nr.circuit ? `${nr.circuit} · Round ${nr.round_number}` : '';
  document.getElementById('nr-date').textContent = nr.race_date || '';
  updateCountdown(nr.race_datetime_utc || nr.race_date);

  renderDriverStandings(data.driver_standings || []);
  renderConstructorStandings(data.constructor_standings || []);
}

function renderPositions(positions) {
  const tbody = document.getElementById('positions-body');
  tbody.innerHTML = positions.map(p => `
    <div class="table-row positions-cols">
      <span class="pos-num ${posClass(p.position)}">${p.position}</span>
      <div class="team-bar" style="background:${p.team_color}"></div>
      <div class="driver-name">${p.code}<span class="driver-sub">${p.full_name}</span></div>
      <span class="gap-val">${p.gap || '—'}</span>
      <span class="lap-val">${p.last_lap_time || '—'}</span>
      <div class="tyre-badge tyre-${p.tyre || '?'}">${p.tyre || '?'}</div>
      <span class="pit-val">${p.pit_count}</span>
    </div>`).join('');
}

function renderRCMessages(messages) {
  const container = document.getElementById('rc-messages');
  container.innerHTML = messages.slice(0, 10).map(m => `
    <div class="rc-row">
      <span class="rc-time">${m.time}</span>
      <span class="rc-dot" style="background:${m.flag_color || '#555'}"></span>
      <span class="rc-text">${m.message}</span>
    </div>`).join('');
}

function renderDriverStandings(standings) {
  const el = document.getElementById('driver-standings-body');
  el.innerHTML = standings.map(s => `
    <div class="table-row drivers-cols">
      <span class="pos-num ${posClass(s.position)}">${s.position}</span>
      <div class="team-bar" style="background:${s.team_color}"></div>
      <div><div class="driver-name">${s.full_name}</div><div class="driver-team">${s.team}</div></div>
      <span class="pts-val">${s.points}</span>
    </div>`).join('');
}

function renderConstructorStandings(standings) {
  const el = document.getElementById('constructor-standings-body');
  el.innerHTML = standings.map(s => `
    <div class="table-row constructors-cols">
      <span class="pos-num ${posClass(s.position)}">${s.position}</span>
      <div style="display:flex;align-items:center;gap:8px">
        <div class="team-bar" style="background:${s.team_color}"></div>
        <span class="driver-name">${s.name}</span>
      </div>
      <span class="pts-val">${s.points}</span>
    </div>`).join('');
}

function updateCountdown(raceDateStr) {
  if (!raceDateStr) return;
  try {
    const raceDate = new Date(raceDateStr);
    const now = new Date();
    const diff = raceDate - now;
    if (diff <= 0) { document.getElementById('nr-countdown').textContent = 'Race day!'; return; }
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    document.getElementById('nr-countdown').textContent = `${d}d ${h}h ${m}m`;
  } catch (e) {}
}

function posClass(pos) {
  if (pos === 1) return 'gold';
  if (pos === 2) return 'silver';
  if (pos === 3) return 'bronze';
  return '';
}

function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

document.getElementById('delay-slider').addEventListener('input', function () {
  document.getElementById('delay-value').textContent = this.value;
});
document.getElementById('delay-slider').addEventListener('change', async function () {
  const val = parseFloat(this.value);
  if (replayMode) {
    await fetch('/api/replay/offset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: val }),
    });
  } else {
    _savedDelay = val;
    await fetch('/api/config/delay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: val }),
    });
  }
});

document.getElementById('btn-live').addEventListener('click', () => { manualView = 'live'; fetchState(); });
document.getElementById('btn-replay').addEventListener('click', () => {
  manualView = 'replay';
  fetchState();
  _loadReplaySessions();
});
document.getElementById('btn-standings').addEventListener('click', () => { manualView = 'standings'; fetchState(); });

document.getElementById('btn-settings').addEventListener('click', () => {
  document.getElementById('settings-overlay').classList.add('open');
  loadSettings();
});
document.getElementById('btn-close-settings').addEventListener('click', () => {
  document.getElementById('settings-overlay').classList.remove('open');
});
document.getElementById('settings-overlay').addEventListener('click', function (e) {
  if (e.target === this) this.classList.remove('open');
});

function _applyVersionInfo(update) {
  const nav = document.getElementById('nav-version');
  if (nav) {
    nav.textContent = update.current ? `v${update.current}` : 'v—';
    if (update.update_available) {
      nav.textContent += ' · Update available';
      nav.style.color = '#FFD700';
    } else {
      nav.style.color = '';
    }
  }
  const cur = document.getElementById('update-current');
  if (cur) cur.textContent = update.current || '—';
}

async function loadNavVersion() {
  try {
    const resp = await fetch('/api/update/check');
    if (!resp.ok) return;
    _applyVersionInfo(await resp.json());
  } catch (e) {}
}

async function loadSettings() {
  try {
    const [updateResp, configResp] = await Promise.all([
      fetch('/api/update/check'),
      fetch('/api/config'),
    ]);
    const update = await updateResp.json();
    _applyVersionInfo(update);
    const btn = document.getElementById('btn-update');
    if (update.update_available) {
      btn.textContent = `Update to ${update.latest}`;
      btn.disabled = false;
    } else {
      btn.textContent = 'Up to date';
      btn.disabled = true;
    }
    const cfg = await configResp.json();
    const slider = document.getElementById('delay-slider');
    slider.value = cfg.delay_seconds ?? 0;
    document.getElementById('delay-value').textContent = slider.value;
  } catch (e) {}
}

document.getElementById('btn-update').addEventListener('click', async () => {
  const btn = document.getElementById('btn-update');
  btn.textContent = 'Updating…';
  btn.disabled = true;
  try { await fetch('/api/update/apply', { method: 'POST' }); } catch (e) {}

  // Service restarts after update — poll until it comes back, then confirm
  const versionEl = document.getElementById('update-current');
  await new Promise(r => setTimeout(r, 5000));
  let attempts = 0;
  const poll = setInterval(async () => {
    attempts++;
    try {
      const resp = await fetch('/api/update/check');
      if (!resp.ok) return;
      const data = await resp.json();
      versionEl.textContent = data.current || '—';
      if (!data.update_available) {
        clearInterval(poll);
        btn.textContent = 'Up to date';
      }
    } catch (e) { /* still restarting */ }
    if (attempts >= 40) { clearInterval(poll); location.reload(); }
  }, 3000);
});

document.querySelectorAll('[data-effect]').forEach(btn => {
  btn.addEventListener('click', async () => {
    await fetch('/api/test-effect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flag_state: btn.dataset.effect }),
    });
  });
});

document.getElementById('btn-test-idle').addEventListener('click', async () => {
  await fetch('/api/test-idle', { method: 'POST' });
});

document.getElementById('btn-test-race-start').addEventListener('click', async () => {
  await fetch('/api/test-race-start', { method: 'POST' });
});

// ── Demo mode ──────────────────────────────────────────────────────────────
let _ledPollInterval = null;
let _ledPixels = [];
let _ledInitCount = 0;

function _initLedStrip(count, segmentBreaks) {
  const track = document.getElementById('led-track');
  if (!track || _ledInitCount === count) return;
  track.innerHTML = '';
  _ledPixels = [];
  _ledInitCount = count;
  const breaks = new Set(segmentBreaks || []);
  for (let i = 0; i < count; i++) {
    const el = document.createElement('div');
    el.className = 'led-pixel';
    track.appendChild(el);
    _ledPixels.push(el);
    if (breaks.has(i)) {
      const div = document.createElement('div');
      div.className = 'led-segment-divider';
      track.appendChild(div);
    }
  }
}

async function _pollLedState() {
  try {
    const resp = await fetch('/api/led-state');
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.available || !data.pixels.length) return;
    _initLedStrip(data.pixels.length, data.segment_breaks);
    for (let i = 0; i < _ledPixels.length; i++) {
      const [r, g, b] = data.pixels[i];
      _ledPixels[i].style.background = `rgb(${r},${g},${b})`;
      _ledPixels[i].style.boxShadow = (r + g + b > 30)
        ? `0 0 4px rgba(${r},${g},${b},0.5)` : 'none';
    }
  } catch (e) {}
}

function _setDemoMode(enabled) {
  const panel = document.getElementById('led-strip-panel');
  const btn = document.getElementById('btn-demo-mode');
  if (enabled) {
    panel.classList.add('visible');
    if (!_ledPollInterval) _ledPollInterval = setInterval(_pollLedState, 100);
    if (btn) { btn.textContent = 'ON'; btn.classList.add('on'); }
  } else {
    panel.classList.remove('visible');
    if (_ledPollInterval) { clearInterval(_ledPollInterval); _ledPollInterval = null; }
    if (btn) { btn.textContent = 'OFF'; btn.classList.remove('on'); }
  }
}

function _setLedEnabled(enabled) {
  const btn = document.getElementById('btn-led-toggle');
  if (!btn) return;
  btn.textContent = enabled ? 'ON' : 'OFF';
  if (enabled) btn.classList.add('on');
  else btn.classList.remove('on');
}

document.getElementById('btn-demo-mode').addEventListener('click', async () => {
  const btn = document.getElementById('btn-demo-mode');
  const enabling = !btn.classList.contains('on');
  await fetch('/api/config/demo-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: enabling }),
  });
  await fetchState();
});

document.getElementById('btn-led-toggle').addEventListener('click', async () => {
  const btn = document.getElementById('btn-led-toggle');
  const enabling = !btn.classList.contains('on');
  await fetch('/api/led/enabled', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: enabling }),
  });
  await fetchState();
});

// ── Shutdown ────────────────────────────────────────────────────────────────
document.getElementById('btn-shutdown').addEventListener('click', async () => {
  if (!confirm('Shut down the Raspberry Pi?\n\nWait 30 seconds before unplugging power.')) return;
  const btn = document.getElementById('btn-shutdown');
  btn.textContent = 'Shutting down…';
  btn.disabled = true;
  try { await fetch('/api/shutdown', { method: 'POST' }); } catch (e) {}
});

document.getElementById('btn-send-logs').addEventListener('click', async () => {
  const btn = document.getElementById('btn-send-logs');
  btn.textContent = 'Sending…';
  btn.disabled = true;
  try {
    const resp = await fetch('/api/logs');
    if (!resp.ok) throw new Error('fetch failed');
    const data = await resp.json();
    const subject = encodeURIComponent(`RaceFlag Diagnostic Logs — ${data.timestamp}`);
    const MAX_CHARS = 2000;
    const raw = data.lines.length > MAX_CHARS
      ? `[logs truncated — showing last ${MAX_CHARS} chars]\n` + data.lines.slice(-MAX_CHARS)
      : data.lines;
    const body = encodeURIComponent(raw);
    window.location.href = `mailto:prometheusprinting.yyc@gmail.com?subject=${subject}&body=${body}`;
  } catch (e) {}
  btn.textContent = 'Send Logs';
  btn.disabled = false;
});

// ── Replay ──────────────────────────────────────────────────────────────────
async function _loadReplaySessions() {
  const dropdown = document.getElementById('replay-dropdown');
  if (!dropdown) return;
  dropdown.innerHTML = '<option value="">Loading…</option>';
  document.getElementById('btn-replay-load').disabled = true;
  try {
    const resp = await fetch('/api/replay/sessions');
    if (!resp.ok) throw new Error('fetch failed');
    const sessions = await resp.json();
    dropdown.innerHTML = '<option value="">Select a race…</option>' +
      sessions.map(s =>
        `<option value="${s.path}" data-name="${s.name}">${s.name} · ${s.date}</option>`
      ).join('');
  } catch (e) {
    dropdown.innerHTML = '<option value="">Failed to load</option>';
  }
}

document.getElementById('replay-dropdown').addEventListener('change', function () {
  document.getElementById('btn-replay-load').disabled = !this.value;
});

document.getElementById('btn-replay-load').addEventListener('click', async () => {
  const dropdown = document.getElementById('replay-dropdown');
  const path = dropdown.value;
  const name = dropdown.options[dropdown.selectedIndex]?.dataset?.name || '';
  if (!path) return;
  const btn = document.getElementById('btn-replay-load');
  btn.textContent = 'Loading…';
  btn.disabled = true;
  try {
    await fetch('/api/replay/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_path: path, session_name: name }),
    });
    document.getElementById('replay-race-chip').textContent = name;
    await fetchState();
  } catch (e) {
    btn.textContent = 'Load';
    btn.disabled = false;
  }
});

document.getElementById('btn-replay-play').addEventListener('click', async () => {
  await fetch('/api/replay/play', { method: 'POST' });
  await fetchState();
});

document.getElementById('btn-replay-pause').addEventListener('click', async () => {
  const isPaused = document.getElementById('btn-replay-pause').textContent.includes('Resume');
  await fetch(isPaused ? '/api/replay/resume' : '/api/replay/pause', { method: 'POST' });
  await fetchState();
});

document.getElementById('btn-replay-stop').addEventListener('click', async () => {
  await fetch('/api/replay/stop', { method: 'POST' });
  await fetchState();
  _loadReplaySessions();
});

fetchState();
loadNavVersion();
(async () => {
  try {
    const resp = await fetch('/api/config');
    if (!resp.ok) return;
    const cfg = await resp.json();
    _savedDelay = cfg.delay_seconds ?? 0;
    const slider = document.getElementById('delay-slider');
    slider.value = _savedDelay;
    document.getElementById('delay-value').textContent = slider.value;
  } catch (e) {}
})();
setInterval(fetchState, POLL_MS);
setInterval(_tickClock, 1000);
