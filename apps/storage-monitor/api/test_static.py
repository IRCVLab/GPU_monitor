from http.client import HTTPConnection
from threading import Thread
from pathlib import Path
import runpy

from api import routes
from api.server import Runtime, Server


def test_static_matches_release():
    manifest = runpy.run_path(str(routes.VIEWER_DIR.parent / "deploy/server/storage_dashboard_release.py"))
    shipped = {name for name in manifest["BUILD_RELEASE_FILES"] if name.startswith("viewer/")}
    assert {"viewer/" + p for p in routes.ASSETS} == shipped - {"viewer/serve.py"}


def test_c7_traversal_variants_404(monkeypatch):
    # Exercise only the replacement router. No unlisted request may open a file.
    monkeypatch.setattr(Path, "read_bytes", lambda path: (_ for _ in ()).throw(AssertionError(path)))
    paths = ("/data/hosts.json", "/./data/hosts.json", "/data/", "/x/../data/hosts.json", "//data/hosts.json",
                     "../data/hosts.json", "..//data/hosts.json", "./../data/hosts.json", "x/../../data/hosts.json",
                     "..%2fdata/hosts.json", "%2e%2e/data/hosts.json", "*/../../data/hosts.json", "../data/",
                     "/data%00/hosts.json")
    with Server(("127.0.0.1", 0), Runtime()) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            for method in ("GET", "HEAD"):
                for path in (*paths, "/serve.py", "/debug.html", "/index.html/", "/%zz", "/%", "/%5c", "//index.html"):
                    conn = HTTPConnection(*server.server_address, timeout=3)
                    try:
                        conn.request(method, path)
                        response = conn.getresponse()
                        assert response.status == 404, (method, path)
                        body = response.read()
                        assert bool(body) == (method == "GET")
                    finally:
                        conn.close()
        finally:
            server.shutdown()
            thread.join()
