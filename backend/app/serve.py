"""Run ORBIT for this computer and, optionally, for a headset on the same Wi-Fi (Phase 18).

    python -m backend.app.serve                 # http://localhost:8765 only
    python -m backend.app.serve --lan           # + https://<lan-ip>:8766 with device pairing

Both listeners serve the *same* app object in one process, so the realtime bus and the
assistant's conversations are shared between the laptop and the headset. WebXR needs a
secure context: on the network that means HTTPS, here with a self-signed certificate
generated on first use (the headset shows a warning once; accept it). Devices on the
network must pair with the code printed below before they can use the API.
"""
import argparse
import asyncio
import shutil
import threading
import ipaddress
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import uvicorn

from backend.app.core.container import default_repository
from backend.app.core.secrets import load_env
from backend.app.core.pairing import new_code
from backend.app.main import create_app

RUN = Path(__file__).resolve().parents[2] / ".run"


def lan_ip() -> Optional[str]:
    """The address other devices on this network use to reach this computer."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
            ip = s.getsockname()[0]
        return ip if not ipaddress.ip_address(ip).is_loopback else None
    except OSError:
        return None


def certificate(ip: str) -> List[str]:
    """Self-signed certificate for this LAN address (regenerated when the address changes)."""
    RUN.mkdir(exist_ok=True)
    key, crt, stamp = RUN / "lan-key.pem", RUN / "lan-cert.pem", RUN / "lan-cert.ip"
    if not (key.exists() and crt.exists() and stamp.exists() and stamp.read_text() == ip):
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
            "-keyout", str(key), "-out", str(crt), "-subj", "/CN=ORBIT local",
            "-addext", f"subjectAltName=IP:{ip},DNS:localhost",
        ], check=True, capture_output=True)
        stamp.write_text(ip)
        os.chmod(key, 0o600)
    return [str(key), str(crt)]


def pairing_code(renew: bool = False) -> str:
    """Kept in .run/ so a paired headset stays paired across restarts; --new-code revokes it."""
    if os.environ.get("ORBIT_PAIR_CODE"):
        return os.environ["ORBIT_PAIR_CODE"]
    RUN.mkdir(exist_ok=True)
    f = RUN / "pair-code"
    if renew or not f.exists():
        f.write_text(new_code())
        os.chmod(f, 0o600)
    return f.read_text().strip()


def keep_quest_linked(port: int, stop: "threading.Event", every: float = 4.0) -> None:
    """Keep the Quest's USB link to ORBIT alive. `adb reverse` is lost whenever the cable is
    unplugged or the headset sleeps; the Quest's ORBIT page (http://localhost:8765) then
    reaches nothing and Orbi can't hear you. Re-applied whenever a Quest is attached."""
    adb = shutil.which("adb")
    if adb is None:
        return
    linked = False
    while not stop.is_set():
        try:
            devices = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5).stdout
            attached = any(line.endswith("\tdevice") for line in devices.splitlines())
            if attached:
                rev = subprocess.run([adb, "reverse", "--list"], capture_output=True, text=True, timeout=5).stdout
                if f"tcp:{port}" not in rev:
                    subprocess.run([adb, "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True, timeout=5)
                    subprocess.run([adb, "forward", "tcp:9335", "localabstract:chrome_devtools_remote"], capture_output=True, timeout=5)
                    print("Quest connected over USB: ORBIT is reachable at http://localhost:%d in the headset" % port, flush=True)
                linked = True
            elif linked:
                print("Quest disconnected from USB: plug the cable back in (or use the Wi-Fi address)", flush=True)
                linked = False
        except (OSError, subprocess.SubprocessError):
            pass
        stop.wait(every)


async def serve(args) -> None:
    load_env()  # GEMINI_API_KEY etc. from the git-ignored .env
    code = url = None
    ip = lan_ip() if args.lan else None
    if args.lan and not ip:
        print("No network address found — is Wi-Fi on? Starting for this computer only.", file=sys.stderr)
    if ip:
        code = pairing_code(args.new_code)
        url = f"https://{ip}:{args.lan_port}/ui/xr.html"
    app = create_app(default_repository(), pair_code=code, lan_url=url)
    configs = [uvicorn.Config(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=2, log_level="info")]
    if ip:
        key, crt = certificate(ip)
        configs.append(uvicorn.Config(app, host="0.0.0.0", port=args.lan_port, ssl_keyfile=key, ssl_certfile=crt,
                                      timeout_graceful_shutdown=2, log_level="info"))
    servers = [uvicorn.Server(c) for c in configs]
    for s in servers:
        s.install_signal_handlers = lambda: None  # one handler below stops both listeners

    def stop(*_):
        for s in servers:
            s.should_exit = True
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)

    link_stop = threading.Event()
    threading.Thread(target=keep_quest_linked, args=(args.port, link_stop), daemon=True).start()

    print(f"ORBIT on this computer:  http://localhost:{args.port}/ui/", flush=True)
    if ip:
        print(f"ORBIT for the headset:   {url}", flush=True)
        print(f"Pairing code:            {code}", flush=True)
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lan", action="store_true", help="also serve HTTPS on the local network, with device pairing")
    p.add_argument("--lan-port", type=int, default=8766)
    p.add_argument("--new-code", action="store_true", help="issue a new pairing code (unpairs every device)")
    asyncio.run(serve(p.parse_args()))


if __name__ == "__main__":
    main()
