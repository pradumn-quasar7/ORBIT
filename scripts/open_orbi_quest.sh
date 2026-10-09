#!/usr/bin/env bash
# Open Orbi in the Quest headset: the ORBIT page in the Quest browser, AR started, and
# Orbi listening — over the USB cable (developer mode). Usage: scripts/open_orbi_quest.sh
set -euo pipefail
cd "$(dirname "$0")/.."
URL="http://localhost:8765/ui/xr.html"

adb devices | grep -q $'\tdevice$' || { echo "The Quest isn't connected over USB (plug in the cable, allow USB debugging)." >&2; exit 1; }
curl -sf -m 3 http://localhost:8765/voice/status > /dev/null || scripts/run_demo.sh --lan > /dev/null
adb reverse tcp:8765 tcp:8765 > /dev/null
adb forward tcp:9222 localabstract:chrome_devtools_remote > /dev/null
adb shell am start -a android.intent.action.VIEW -d "$URL" com.oculus.browser > /dev/null

.venv/bin/python - <<'EOF'
import json, sys, time, urllib.request
sys.path.insert(0, ".")
from backend.app.core.cdp import CDP

def tab():
    with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3) as r:
        return next((t for t in json.loads(r.read()) if "/ui/xr.html" in t.get("url", "")), None)

for _ in range(20):
    t = tab()
    if t:
        break
    time.sleep(1)
else:
    sys.exit("The ORBIT page didn't open in the Quest browser.")
c = CDP(t["webSocketDebuggerUrl"])
for _ in range(20):  # wait until the page has loaded and is in front
    if c.evaluate("document.readyState === 'complete' && document.visibilityState === 'visible' && !!window.orbiTalk"):
        break
    time.sleep(0.5)
if c.evaluate("document.getElementById('mode').textContent") != "in the headset":
    c.evaluate("document.getElementById('enter').click()", gesture=True)
    time.sleep(4)
mode = c.evaluate("document.getElementById('mode').textContent")
phase = (c.evaluate("window.__orbiVoice") or {}).get("phase")
if phase not in ("listening", "recording", "thinking", "speaking"):
    c.evaluate("window.orbiTalk()", gesture=True)
    time.sleep(4)
v = c.evaluate("window.__orbiVoice") or {}
status = c.evaluate("document.getElementById('xr-status').textContent")
ar = "running" if mode == "in the headset" else "not started (" + status + ")"
print("Orbi: AR " + ar + ", voice " + str(v.get("phase", "off")) + ", mic level " + str(v.get("rms")))
EOF
