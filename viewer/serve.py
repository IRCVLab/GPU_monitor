#!/usr/bin/env python3
"""Static server for the storage-viz dashboard.

- Disables caching so lab members always get the latest page/JSON on a refresh.
- Adds an on-demand rescan: POST /rescan runs the scanner (this process runs as
  root, so the scan is complete), GET /rescan-status reports progress. The
  dashboard's "Rescan" button drives these.
Usage: serve.py [PORT]
"""
import http.server, socketserver, sys, os, json, threading, subprocess, time, socket

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
DIRECTORY = os.path.dirname(os.path.abspath(__file__))      # .../viewer
PROJ = os.path.dirname(DIRECTORY)                            # project root
SCANNER = os.path.join(PROJ, "scanner", "hstscan")
DATA = os.path.join(PROJ, "data", socket.gethostname() + ".json")
TARGETS = ["/", "/data", "/data1", "/data3"]

state = {"scanning": False, "started": 0, "finished": 0, "duration": 0, "error": None}
lock = threading.Lock()


def run_scan():
    t0 = time.time()
    err = None
    try:
        r = subprocess.run([SCANNER, "--out", DATA] + TARGETS,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace")[-400:]
    except Exception as e:
        err = str(e)
    with lock:
        state.update(scanning=False, finished=time.time(),
                     duration=round(time.time() - t0, 1), error=err)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DIRECTORY, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] == "/rescan":
            with lock:
                if state["scanning"]:
                    return self._json({"status": "already_running", "started": state["started"]})
                state.update(scanning=True, started=time.time(), error=None)
            threading.Thread(target=run_scan, daemon=True).start()
            return self._json({"status": "started"})
        self._json({"error": "not found"}, 404)

    def do_GET(self):
        if self.path.split("?")[0] == "/rescan-status":
            with lock:
                return self._json(dict(state))
        super().do_GET()

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        print(f"storage-viz serving {DIRECTORY} on :{PORT} (no-cache, /rescan enabled)", flush=True)
        httpd.serve_forever()
