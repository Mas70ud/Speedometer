# SPEED – Real‑time GPS Speedometer for Termux

Minimal speedometer that uses `termux-location` (GPS) and serves a live dashboard on the first available port ≥10001.  
Automatically logs speed every 5 seconds, calculates total distance traveled, and shows a 1‑hour speed trend.

## Features
- Live analog/digital speedometer (max 160 km/h)
- Distance traveled (odometer) from GPS coordinate changes
- Speed logging to `log/YYYYMMDD_HHMMSS.txt` every 5 seconds
- GPS status, accuracy, provider quality bar
- 1‑hour speed history chart
- Auto‑opens browser on start (supports `termux-open-url`)

## Requirements (on Android with Termux)
```bash
pkg install python termux-api
pip install flask
```

Usage

1. Allow location permission for Termux (Android settings)
2. Run the script:
   ```bash
   python speedometer.py
   ```
3. Browser opens automatically – start moving (GPS must be active)

Notes

· No simulation mode – real GPS only (speed = 0 when inactive)
· Ports scanned from 10001 upward
· Distance jumps >500 m are ignored (prevents glitches)

Log file example

```
2025-02-18 15:30:05 : 42
2025-02-18 15:30:10 : 44
...
```
