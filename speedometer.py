#!/usr/bin/env python3
"""
Real-time speedometer using GPS from Termux:API.
Serves the gauge on the first available port >= 10001.
Auto-opens the browser once the server is ready.
Styled with simple SPEED branding.
Auto-logs speed every 5 seconds into a timestamped file inside 'log/'.
Distance traveled is accumulated from GPS coordinate changes.
No simulation mode – only real GPS data (Active/Inactive).
"""

import json
import math
import subprocess
import threading
import time
import os
import socket
import webbrowser
from collections import deque
from datetime import datetime
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ----------------------------
# Logging setup
# ----------------------------
LOG_DIR = "log"
os.makedirs(LOG_DIR, exist_ok=True)

start_time = datetime.now()
log_filename = start_time.strftime("%Y%m%d_%H%M%S") + ".txt"
log_filepath = os.path.join(LOG_DIR, log_filename)

log_lock = threading.Lock()


def log_speed_periodically():
    """Every 5 seconds, write current timestamp and speed (km/h) to log file."""
    while True:
        time.sleep(5)
        current_speed = speed_kmh
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp} : {current_speed}\n"
        with log_lock:
            with open(log_filepath, "a", encoding="utf-8") as f:
                f.write(log_line)
                f.flush()


# ----------------------------
# Speed smoothing (only for active GPS)
# ----------------------------
class SpeedSmoother:
    def __init__(self, window_size=5):
        self.speeds = deque(maxlen=window_size)
        self.last_valid_speed = 0

    def add_speed(self, speed):
        if speed is not None:
            self.speeds.append(speed)
            self.last_valid_speed = speed

        if len(self.speeds) > 0:
            weights = list(range(1, len(self.speeds) + 1))
            weighted_sum = sum(s * w for s, w in zip(self.speeds, weights))
            weight_total = sum(weights)
            return round(weighted_sum / weight_total, 1)

        return self.last_valid_speed


speed_smoother = SpeedSmoother(window_size=8)

# ----------------------------
# Global state
# ----------------------------
speed_kmh = 0
total_distance_km = 0.0           # odometer in kilometers
last_lat = None
last_lon = None
gps_status = {
    'connected': False,            # True = Active, False = Inactive
    'accuracy': 0,
    'provider': 'none',
    'last_update': 'never'
}


def haversine(lat1, lon1, lat2, lon2):
    """Return distance in kilometers between two points using Haversine formula."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_gps_data():
    """
    Read full GPS data from termux-location.
    Returns dict with speed (km/h), accuracy, provider, lat, lon, or None on failure.
    Increased timeout to 5 seconds to avoid frequent timeouts.
    """
    try:
        # استفاده از تایم‌اوت ۵ ثانیه برای گرفتن موقعیت
        raw = subprocess.check_output(['termux-location'], timeout=5)
        data = json.loads(raw)

        result = {
            'speed': None,
            'accuracy': 0,
            'provider': 'unknown',
            'lat': None,
            'lon': None
        }

        # Speed (m/s -> km/h)
        if 'speed' in data and data['speed'] is not None:
            speed_ms = float(data['speed'])
            if 0 <= speed_ms < 100:
                result['speed'] = speed_ms * 3.6

        # Accuracy
        if 'accuracy' in data:
            acc = float(data['accuracy'])
            if 0 < acc < 1000:
                result['accuracy'] = round(acc, 1)

        # Provider
        if 'provider' in data:
            result['provider'] = data['provider']

        # Latitude & Longitude
        if 'latitude' in data and 'longitude' in data:
            result['lat'] = float(data['latitude'])
            result['lon'] = float(data['longitude'])

        return result

    except subprocess.TimeoutExpired:
        # خطای timeout را بیصدا نادیده میگیریم (فقط برای دیباگ چاپ کنیم)
        # print("GPS timeout (normal if no fix yet)")
        return None
    except subprocess.CalledProcessError:
        return None
    except json.JSONDecodeError:
        return None
    except Exception as e:
        # فقط خطاهای غیرمنتظره را چاپ کن
        print(f"Unexpected GPS error: {e}")
        return None


def update_gps_state():
    global speed_kmh, gps_status, total_distance_km, last_lat, last_lon

    consecutive_failures = 0

    while True:
        gps_data = get_gps_data()

        # Check if we have valid GPS data with speed
        if gps_data is not None and gps_data['speed'] is not None:
            # Real GPS with valid speed -> ACTIVE
            smoothed_speed = speed_smoother.add_speed(gps_data['speed'])
            speed_kmh = round(smoothed_speed)
            gps_status = {
                'connected': True,
                'accuracy': gps_data['accuracy'],
                'provider': gps_data['provider'],
                'last_update': datetime.now().strftime('%H:%M:%S')
            }
            consecutive_failures = 0

            # Update distance traveled if we have valid lat/lon
            if gps_data['lat'] is not None and gps_data['lon'] is not None:
                if last_lat is not None and last_lon is not None:
                    dist = haversine(last_lat, last_lon, gps_data['lat'], gps_data['lon'])
                    if dist < 0.5:  # ignore jumps > 500m
                        total_distance_km += dist
                last_lat = gps_data['lat']
                last_lon = gps_data['lon']

        else:
            # No valid GPS speed -> INACTIVE
            consecutive_failures += 1
            speed_kmh = 0
            # Reset smoother so that when GPS comes back, it starts fresh
            speed_smoother.speeds.clear()
            # Reset last known position to avoid invalid distance jumps
            last_lat = None
            last_lon = None

            gps_status = {
                'connected': False,
                'accuracy': 0,
                'provider': 'none',
                'last_update': datetime.now().strftime('%H:%M:%S')
            }

        # Sleep dynamically: faster when inactive (2 sec), normal when active (0.5 sec)
        sleep_time = 0.5 if gps_status['connected'] else 2.0
        time.sleep(sleep_time)


# Start background threads
threading.Thread(target=update_gps_state, daemon=True).start()
threading.Thread(target=log_speed_periodically, daemon=True).start()

# ----------------------------
# Flask UI (same as before, no changes needed)
# ----------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport"
        content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>SPEED</title>
  <style>
    :root {
      --bg: #0F0F10;
      --surface: #1A1A1C;
      --surface-2: #151517;
      --text: #F5F5F5;
      --muted: #9A9AA0;
      --line: #2A2A2E;
      --brand: #D95A41;
      --brand-soft: #F2A694;
      --ok: #58C27D;
      --warn: #F2A694;
      --bad: #D95A41;
    }

    * {
      box-sizing: border-box;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
      -webkit-user-select: none;
    }

    html, body {
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      touch-action: none;
      overscroll-behavior: none;
      position: fixed;
    }

    body {
      min-height: 100dvh;
    }

    .app {
      width: 100vw;
      height: 100dvh;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background:
        radial-gradient(circle at top, rgba(217,90,65,0.08), transparent 35%),
        linear-gradient(180deg, #101012 0%, #0F0F10 100%);
    }

    .shell {
      width: min(100%, 430px);
      height: min(100dvh - 20px, 860px);
      background: linear-gradient(180deg, rgba(26,26,28,0.96), rgba(20,20,22,0.98));
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      box-shadow:
        0 0 0 1px rgba(255,255,255,0.02) inset,
        0 20px 60px rgba(0,0,0,0.45);
      overflow-y: auto;
      overflow-x: hidden;
    }

    .shell::-webkit-scrollbar {
      width: 0;
      background: transparent;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 4px 2px 0 2px;
      flex-shrink: 0;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--muted);
    }

    .brand-mark {
      width: 26px;
      height: 26px;
      border-radius: 9px;
      background: var(--surface-2);
      border: 1px solid var(--line);
      display: grid;
      place-items: center;
      color: var(--brand);
      font-weight: 800;
      font-size: 15px;
      line-height: 1;
    }

    /* no mode-badge anymore */

    .gauge-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    svg {
      width: 100%;
      max-width: 390px;
      height: auto;
      display: block;
    }

    .speed-value {
      fill: var(--text);
      font-size: 44px;
      font-weight: 800;
      letter-spacing: -0.03em;
    }

    .speed-unit {
      fill: var(--muted);
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }

    .tick-text {
      fill: #B6B6BC;
      font-size: 10px;
      text-anchor: middle;
      font-weight: 600;
    }

    .chart-container {
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 10px;
      margin-top: 4px;
      flex-shrink: 0;
    }

    .chart-title {
      color: var(--muted);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 8px;
      padding-left: 4px;
    }

    canvas {
      width: 100%;
      height: auto;
      display: block;
      border-radius: 12px;
    }

    .bottom {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      flex-shrink: 0;
    }

    .card {
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      min-width: 0;
      box-shadow: 0 0 0 1px rgba(255,255,255,0.015) inset;
    }

    .label {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }

    .value {
      color: var(--text);
      font-size: 18px;
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .value.small {
      font-size: 15px;
      font-weight: 700;
    }

    .status-inline {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .gps-indicator {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex: 0 0 auto;
    }

    .gps-active {
      background: var(--ok);
      box-shadow: 0 0 12px rgba(88,194,125,0.55);
    }

    .gps-inactive {
      background: var(--bad);
      box-shadow: 0 0 12px rgba(217,90,65,0.45);
    }

    .accuracy-good { color: var(--ok); }
    .accuracy-medium { color: var(--warn); }
    .accuracy-poor { color: var(--bad); }

    .quality-bar-container {
      margin-top: 10px;
      width: 100%;
      background: #2A2A2E;
      border-radius: 4px;
      height: 4px;
      overflow: hidden;
    }
    .quality-bar-fill {
      width: 0%;
      height: 100%;
      background: var(--brand);
      border-radius: 4px;
      transition: width 0.2s ease;
    }

    @media (max-height: 700px) {
      .shell {
        padding: 14px;
        gap: 12px;
        border-radius: 22px;
      }
      .speed-value {
        font-size: 38px;
      }
      .card {
        padding: 12px;
      }
      .chart-container {
        padding: 8px;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="shell">
      <div class="topbar">
        <div class="brand">
          <div class="brand-mark">S</div>
          <div>SPEED</div>
        </div>
        <!-- removed mode-badge -->
      </div>

      <div class="gauge-wrap">
        <svg viewBox="0 0 400 320" aria-label="speedometer">
          <path d="M 50 200 A 150 150 0 0 1 350 200"
                fill="none" stroke="#26262B" stroke-width="14" stroke-linecap="round"/>
          <path d="M 50 200 A 150 150 0 0 1 350 200"
                fill="none" stroke="#D95A41" stroke-opacity="0.16" stroke-width="6" stroke-linecap="round"/>

          {% for i in range(0, 161, 20) %}
            {% set angle = math.pi + (i/160) * math.pi %}
            {% set x1 = 200 + 140 * math.cos(angle) %}
            {% set y1 = 200 + 140 * math.sin(angle) %}
            {% set x2 = 200 + 127 * math.cos(angle) %}
            {% set y2 = 200 + 127 * math.sin(angle) %}
            {% set tx = 200 + 112 * math.cos(angle) %}
            {% set ty = 200 + 112 * math.sin(angle) %}
            <line x1="{{ x1 }}" y1="{{ y1 }}" x2="{{ x2 }}" y2="{{ y2 }}"
                  stroke="#7A7A82" stroke-width="2" stroke-linecap="round"/>
            <text x="{{ tx }}" y="{{ ty }}" dy="3" class="tick-text">{{ i }}</text>
          {% endfor %}

          <path id="progressArc" d="" fill="none" stroke="#D95A41" stroke-width="8" stroke-linecap="round"/>
          <line id="needle" x1="200" y1="200" x2="200" y2="74" stroke="#D95A41" stroke-width="4" stroke-linecap="round"/>
          <circle cx="200" cy="200" r="14" fill="#111113" stroke="#2A2A2E" stroke-width="2"/>
          <circle cx="200" cy="200" r="6" fill="#D95A41"/>
          <text id="speedValue" class="speed-value" x="200" y="258" text-anchor="middle">0</text>
          <text class="speed-unit" x="200" y="278" text-anchor="middle">km/h</text>
        </svg>
      </div>

      <!-- Speed trend chart (last hour) -->
      <div class="chart-container">
        <div class="chart-title">📈 Speed trend (last hour) km/h</div>
        <canvas id="speedChart" width="500" height="140" style="width:100%; height:auto; max-width:100%;"></canvas>
      </div>

      <div class="bottom">
        <div class="card">
          <div class="label">GPS Status</div>
          <div class="value small status-inline">
            <span id="gpsStatus">Inactive</span>
            <span id="gpsDot" class="gps-indicator gps-inactive"></span>
          </div>
        </div>

        <!-- Distance traveled card -->
        <div class="card">
          <div class="label">Distance Traveled</div>
          <div id="distanceValue" class="value">0.0 km</div>
        </div>

        <div class="card">
          <div class="label">Accuracy</div>
          <div id="accuracyValue" class="value">--</div>
        </div>

        <div class="card">
          <div class="label">Provider</div>
          <div class="value small" id="providerName">--</div>
          <div class="quality-bar-container">
            <div id="qualityBarFill" class="quality-bar-fill" style="width:0%"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    // Speedometer (max 160 km/h)
    const needle = document.getElementById('needle');
    const speedText = document.getElementById('speedValue');
    const progressArc = document.getElementById('progressArc');

    const gpsStatusEl = document.getElementById('gpsStatus');
    const gpsDotEl = document.getElementById('gpsDot');
    const distanceValueEl = document.getElementById('distanceValue');
    const accuracyValueEl = document.getElementById('accuracyValue');
    const providerNameEl = document.getElementById('providerName');
    const qualityBarFill = document.getElementById('qualityBarFill');

    const MAX_SPEED = 160;
    let currentAngle = Math.PI;
    let targetAngle = Math.PI;
    const SMOOTH_FACTOR = 0.12;

    function polarToCartesian(cx, cy, r, angle) {
      return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
    }

    function describeArc(cx, cy, r, startAngle, endAngle) {
      const start = polarToCartesian(cx, cy, r, startAngle);
      const end = polarToCartesian(cx, cy, r, endAngle);
      const largeArcFlag = endAngle - startAngle <= Math.PI ? "0" : "1";
      return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`;
    }

    function updateNeedle(speed) {
      const clamped = Math.min(MAX_SPEED, Math.max(0, speed));
      targetAngle = Math.PI + (clamped / MAX_SPEED) * Math.PI;
      speedText.textContent = Math.round(clamped);
    }

    function animateNeedle() {
      currentAngle += (targetAngle - currentAngle) * SMOOTH_FACTOR;
      const cx = 200, cy = 200, r = 126;
      const x = cx + r * Math.cos(currentAngle);
      const y = cy + r * Math.sin(currentAngle);
      needle.setAttribute('x2', x);
      needle.setAttribute('y2', y);
      const arcPath = describeArc(200, 200, 150, Math.PI, currentAngle);
      progressArc.setAttribute('d', arcPath);
      requestAnimationFrame(animateNeedle);
    }

    // Speed chart (1 hour history)
    const canvas = document.getElementById('speedChart');
    const ctx = canvas.getContext('2d');
    let speedHistory = [];
    let lastStoreTime = 0;

    function addSpeedToHistory(speed) {
      const now = Date.now();
      if (speedHistory.length === 0 || (now - lastStoreTime) >= 5000) {
        speedHistory.push({ timestamp: new Date(now), speed: speed });
        lastStoreTime = now;
        const oneHourAgo = now - 60 * 60 * 1000;
        speedHistory = speedHistory.filter(p => p.timestamp.getTime() >= oneHourAgo);
        drawChart();
      }
    }

    function drawChart() {
      if (!ctx || speedHistory.length === 0) {
        if (ctx) {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = '#9A9AA0';
          ctx.font = '10px Inter, sans-serif';
          ctx.fillText('Collecting data...', 10, 20);
        }
        return;
      }

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      
      let maxY = Math.max(...speedHistory.map(p => p.speed), 20);
      maxY = Math.min(Math.ceil(maxY / 20) * 20, MAX_SPEED);
      
      const startTime = speedHistory[0].timestamp.getTime();
      const endTime = speedHistory[speedHistory.length-1].timestamp.getTime();
      const timeRange = Math.max(endTime - startTime, 1);
      
      const mapX = (timestamp) => ((timestamp - startTime) / timeRange) * w;
      const mapY = (speed) => h - (speed / maxY) * h;
      
      ctx.beginPath();
      ctx.strokeStyle = '#2A2A2E';
      ctx.lineWidth = 1;
      for (let val = 0; val <= maxY; val += 20) {
        const y = mapY(val);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        ctx.fillStyle = '#7A7A82';
        ctx.font = '8px Inter, sans-serif';
        ctx.fillText(val, 4, y - 2);
      }
      
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(0, h);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, h);
      ctx.lineTo(w, h);
      ctx.stroke();
      
      ctx.beginPath();
      ctx.strokeStyle = '#D95A41';
      ctx.lineWidth = 2;
      let first = true;
      for (let i = 0; i < speedHistory.length; i++) {
        const x = mapX(speedHistory[i].timestamp.getTime());
        const y = mapY(speedHistory[i].speed);
        if (first) {
          ctx.moveTo(x, y);
          first = false;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
      
      ctx.fillStyle = '#F2A694';
      for (let i = 0; i < speedHistory.length; i++) {
        const x = mapX(speedHistory[i].timestamp.getTime());
        const y = mapY(speedHistory[i].speed);
        ctx.beginPath();
        ctx.arc(x, y, 2, 0, 2 * Math.PI);
        ctx.fill();
      }
      
      const nowTime = endTime;
      for (let offset = -60; offset <= 0; offset += 15) {
        const timePoint = nowTime + offset * 60 * 1000;
        if (timePoint >= startTime && timePoint <= endTime) {
          const x = mapX(timePoint);
          const date = new Date(timePoint);
          const label = date.getHours().toString().padStart(2,'0') + ':' + date.getMinutes().toString().padStart(2,'0');
          ctx.fillStyle = '#9A9AA0';
          ctx.font = '8px Inter, sans-serif';
          ctx.fillText(label, x - 12, h - 4);
        }
      }
    }

    function resizeCanvas() {
      const container = canvas.parentElement;
      const containerWidth = container.clientWidth - 16;
      canvas.width = Math.max(300, containerWidth);
      canvas.height = 130;
      drawChart();
    }
    window.addEventListener('resize', () => resizeCanvas());
    
    // GPS quality bar (based on accuracy, only when active)
    function computeQuality(accuracy, connected) {
      if (!connected) return 0;
      if (accuracy <= 5) return 100;
      if (accuracy <= 10) return 80;
      if (accuracy <= 20) return 60;
      if (accuracy <= 50) return 40;
      return 20;
    }
    
    function updateStatus(status, distance) {
      if (status.connected) {
        gpsStatusEl.textContent = 'Active';
        gpsDotEl.className = 'gps-indicator gps-active';
      } else {
        gpsStatusEl.textContent = 'Inactive';
        gpsDotEl.className = 'gps-indicator gps-inactive';
      }

      // Distance display
      let distKm = distance;
      if (distKm < 1.0) {
        distanceValueEl.textContent = Math.round(distKm * 1000) + ' m';
      } else {
        distanceValueEl.textContent = distKm.toFixed(1) + ' km';
      }

      const accuracy = status.accuracy;
      if (accuracy > 0 && status.connected) {
        let accuracyClass = 'accuracy-good';
        if (accuracy > 20) accuracyClass = 'accuracy-poor';
        else if (accuracy > 10) accuracyClass = 'accuracy-medium';
        accuracyValueEl.className = 'value ' + accuracyClass;
        accuracyValueEl.textContent = accuracy + ' m';
      } else {
        accuracyValueEl.className = 'value';
        accuracyValueEl.textContent = '--';
      }

      providerNameEl.textContent = status.provider && status.provider !== 'none' ? status.provider.toUpperCase() : '--';
      const quality = computeQuality(accuracy, status.connected);
      qualityBarFill.style.width = quality + '%';
    }

    function fetchData() {
      fetch('/speed')
        .then(response => response.json())
        .then(data => {
          updateNeedle(data.speed);
          updateStatus(data.status, data.distance);
          addSpeedToHistory(data.speed);
        })
        .catch(err => console.error('Fetch error:', err));
    }

    // Prevent zoom and page movement
    document.addEventListener('gesturestart', e => e.preventDefault());
    document.addEventListener('gesturechange', e => e.preventDefault());
    document.addEventListener('gestureend', e => e.preventDefault());
    document.addEventListener('touchmove', e => e.preventDefault(), { passive: false });
    document.addEventListener('dblclick', e => e.preventDefault());

    animateNeedle();
    resizeCanvas();
    fetchData();
    setInterval(fetchData, 250);
  </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, math=math, range=range)


@app.route('/speed')
def speed():
    return jsonify({
        'speed': speed_kmh,
        'distance': round(total_distance_km, 3),
        'status': gps_status
    })


# ----------------------------
# Port finding and browser launch helpers
# ----------------------------
def find_free_port(start_port):
    """Return the first free port starting from start_port (inclusive)."""
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1


def open_browser(url):
    """Try to open URL using Termux API if available, otherwise fallback to webbrowser."""
    try:
        subprocess.run(['termux-open-url', url], check=False, timeout=2)
        print(f"Opened browser using termux-open-url: {url}")
    except (FileNotFoundError, subprocess.SubprocessError):
        webbrowser.open(url)
        print(f"Opened browser using system default: {url}")


if __name__ == '__main__':
    selected_port = find_free_port(10001)
    url = f"http://localhost:{selected_port}"

    print(f"Starting SPEED server on {url}")
    print(f"Logging speed every 5 seconds into: {log_filepath}")
    print("Distance traveled is calculated from real GPS coordinate changes.")
    print("Press Ctrl+C to stop.")

    threading.Timer(1.0, lambda: open_browser(url)).start()
    app.run(host='0.0.0.0', port=selected_port, debug=False, threaded=True)
