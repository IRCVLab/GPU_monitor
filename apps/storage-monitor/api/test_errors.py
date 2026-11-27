"""Every v2 route must reply, including when its dependency fails (I4)."""
import pytest

from api import routes
from api.errors import RequestError
from collector.errors import ActiveJob, Cooldown, GlobalConcurrency, RescanDisabled, ServerBusy, StoreUnavailable, UnknownServer


@pytest.mark.parametrize("error,status,code,retry", [
    (UnknownServer(), 404, "UNKNOWN_SERVER", None),
    (StoreUnavailable(), 503, "STORE_UNAVAILABLE", None),
    (ActiveJob(job={"id": "active"}), 409, "ACTIVE_JOB", None),
    (Cooldown(42), 429, "COOLDOWN", "42"),
    (GlobalConcurrency(), 429, "GLOBAL_CONCURRENCY", "5"),
    (ServerBusy(), 503, "SERVER_BUSY", "5"),
    (RescanDisabled(), 403, "FORBIDDEN", None),
    (RequestError("BAD_REQUEST"), 400, "BAD_REQUEST", None),
    (RequestError("AUTH_REQUIRED"), 401, "AUTH_REQUIRED", None),
    (RequestError("FORBIDDEN"), 403, "FORBIDDEN", None),
    (RequestError("BAD_ORIGIN"), 403, "BAD_ORIGIN", None),
    (RequestError("BAD_CSRF"), 403, "BAD_CSRF", None),
    (RequestError("UNKNOWN_VOLUME"), 404, "UNKNOWN_VOLUME", None),
    (RequestError("CAPTURE_GONE"), 410, "CAPTURE_GONE", None),
    (RequestError("BODY_TOO_LARGE"), 413, "BODY_TOO_LARGE", None),
    (RuntimeError("private diagnostic"), 500, "INTERNAL", None),
])
def test_each_route_answers(collector_api, monkeypatch, error, status, code, retry):
    host, request, _ = collector_api

    def fail(*args, **kwargs):
        raise error

    for method, pattern, action, _ in routes.ROUTES:
        if not pattern.startswith("/api/v2/"):
            continue
        with monkeypatch.context() as patch:
            # Inject at the dependency used by this route. Public compatibility
            # routes have constant bodies, so replace their route function.
            if action in (routes.detail, routes.activity, routes.capture):
                patch.setattr(host.fleet, "view", fail)
            elif action is routes.fleet:
                patch.setattr(host.fleet, "views", fail)
            elif action in (routes.session, routes.rescan):
                from api.server import Handler
                patch.setattr(Handler, "_session" if action is routes.session else "_require_post_auth", fail)
            else:
                patch.setattr(routes, "ROUTES", [(method, pattern, fail, "public")])
            path = pattern.replace("([^/]+)", "alpha-1", 1).replace("([^/]+)", "v-"+"1"*16, 1).replace("([^/]+)", "1"*64, 1)
            actual, body, headers = request(method, path, "{}" if method == "POST" else None)
            assert actual == status
            assert body["error"]["code"] == code
            assert body["error"]["request_id"] == headers["X-Request-Id"]
            assert headers.get("Retry-After") == retry
            assert headers["X-Request-Id"]


def test_unmatched_and_wrong_method_errors_have_request_id(collector_api):
    _, request, _ = collector_api
    for method, path, expected, code in [
        ("GET", "/missing", 404, "NOT_FOUND"),
        ("PATCH", "/api/v2/fleet", 405, "METHOD_NOT_ALLOWED"),
    ]:
        status, body, headers = request(method, path)
        assert status == expected
        assert body == {"error": {"code": code, "message": code.replace("_", " ").capitalize(),
                                  "request_id": headers["X-Request-Id"]}}
        if expected == 405:
            assert headers["Allow"] == "GET"


def test_unregistered_paths_always_answer(collector_api):
    import random

    _, request, _ = collector_api
    rng = random.Random(4)
    for _ in range(5000):
        suffix = "".join(rng.choices("abc123%.,;+-_", k=rng.randrange(1, 32)))
        status, body, headers = request("GET", "/unregistered/" + suffix)
        assert status == 404
        assert body["error"]["request_id"] == headers["X-Request-Id"]
