const POLL_MS = 2000;
let manualView = null;
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
  document.getElementById('view-live').classList.toggle('active', targetView === 'live');
  document.getElementById('view-standings').classList.toggle('active', targetView === 'standings');
  document.getElementById('btn-live').classList.toggle('active', targetView === 'live');
  document.getElementById('btn-standings').classList.toggle('active', targetView === 'standings');

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
  document.getElementById('session-lap').textContent = s.total_laps ? `Lap ${s.current_lap} / ${s.total_laps}` : '';
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

  renderPositions(data.driver_positions || []);
  renderRCMessages(rcMsgs);

  const nr = data.next_race || {};
  document.getElementById('nr-flag').textContent = nr.country_flag || '';
  document.getElementById('nr-name').textContent = nr.name || '—';
  document.getElementById('nr-meta').textContent = nr.circuit ? `${nr.circuit} · Round ${nr.round_number}` : '';
  document.getElementById('nr-date').textContent = nr.race_date || '';
  updateCountdown(nr.race_date);

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
  await fetch('/api/config/delay', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seconds: parseFloat(this.value) }),
  });
});

document.getElementById('btn-live').addEventListener('click', () => { manualView = 'live'; fetchState(); });
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

async function loadSettings() {
  try {
    const [updateResp, configResp] = await Promise.all([
      fetch('/api/update/check'),
      fetch('/api/config'),
    ]);
    const update = await updateResp.json();
    document.getElementById('update-current').textContent = update.current || '—';
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

fetchState();
setInterval(fetchState, POLL_MS);
setInterval(_tickClock, 1000);
