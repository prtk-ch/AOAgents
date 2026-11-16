#!/usr/bin/env python3
"""Test console for the always-on agent. Runs the loop *and* serves the UI.

    .venv/bin/python ui.py          ->  http://127.0.0.1:8787

Localhost only: the page can fire arbitrary prompts, so it is not something to expose.
"""
import json, os, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aoa

PORT = int(os.environ.get("AOA_PORT", 8787))
HERE = Path(__file__).parent


def creds() -> bool:
    return bool(aoa.BASE_URL and aoa.MODEL)


def snapshot():
    state = aoa.load_state()
    now = time.time()
    trigs = []
    for t in json.loads(aoa.TRIGGERS.read_text()):
        last = state.get(t["name"], 0)
        trigs.append({"name": t["name"], "prompt": t["prompt"],
                      "schedule": f"every {t['every']}s" if "every" in t else f"daily {t['at']}",
                      "last": datetime.fromtimestamp(last).strftime("%H:%M:%S") if last else "never",
                      "in": max(0, int(aoa.next_fire(t, last, now) - now))})
    log = aoa.today_log()
    return {
        "creds": creds(), "model": aoa.MODEL or "(unconfigured)", "endpoint": aoa.BASE_URL,
        "paused": aoa.paused(), "queued": aoa.JOBS.qsize(),
        "shell": aoa.ALLOW_SHELL, "triggers": trigs, "recent": list(aoa.RECENT),
        "memory": (aoa.WS / "MEMORY.md").read_text() if (aoa.WS / "MEMORY.md").exists() else "",
        "log": log.read_text() if log.exists() else "",
        "inbox": sorted(p.name for p in (aoa.WS / "inbox").glob("*") if p.is_file()),
        "outbox": sorted(p.name for p in (aoa.WS / "outbox").glob("*") if p.is_file()),
        "skills": sorted(p.name for p in (aoa.WS / "skills").glob("*.md")),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self._send((HERE / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(json.dumps(snapshot()).encode())
        if self.path.startswith("/api/file?"):
            rel = self.path.split("?", 1)[1]
            try:                                    # _safe keeps this inside workspace/
                return self._send(json.dumps({"text": aoa.read_file(rel)}).encode())
            except (ValueError, OSError) as e:
                return self._send(json.dumps({"text": f"{type(e).__name__}: {e}"}).encode())
        self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        if self.path == "/api/fire":                # run a trigger now, out of schedule
            name = body.get("name", "")
            trig = next((t for t in json.loads(aoa.TRIGGERS.read_text()) if t["name"] == name), None)
            if not trig:
                return self.send_error(400, "unknown trigger")
            aoa.JOBS.put((f"{name} (manual)", trig["prompt"]))
        elif self.path == "/api/once":              # ad-hoc prompt
            aoa.JOBS.put(("manual", body.get("prompt", "").strip() or "Log that you woke up."))
        elif self.path == "/api/pause":             # stop every automatic firing
            aoa.set_paused(bool(body.get("on")))
        elif self.path == "/api/shutdown":          # stop the process itself
            threading.Timer(0.3, lambda: os._exit(0)).start()
        elif self.path == "/api/event":             # event trigger: drop a file in the inbox
            p = aoa.WS / "inbox" / f"{int(time.time())}-ui.txt"
            p.write_text(body.get("text", ""))
        else:
            return self.send_error(404)
        return self._send(b'{"ok":true}')

    def log_message(self, *a):                      # the agent's output is the signal, not HTTP noise
        pass


if __name__ == "__main__":
    threading.Thread(target=aoa.loop, kwargs={"interval": 3}, daemon=True).start()
    print(f"console: http://127.0.0.1:{PORT}   "
          f"({aoa.MODEL + ' @ ' + aoa.BASE_URL if creds() else 'NO endpoint - dry runs'})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
