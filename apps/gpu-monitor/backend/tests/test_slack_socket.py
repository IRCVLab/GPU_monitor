import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.slack_socket import SlackSocketService


class _FakeBoltApp:
    def command(self, _name):
        def decorator(func):
            return func

        return decorator


class _FakeSocketModeHandler:
    def __init__(self, app, app_token):
        self.app = app
        self.app_token = app_token

    def start(self):
        return None

    def close(self):
        return None


class _FakeThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        return None


class SlackSocketServiceTests(unittest.TestCase):
    def test_socket_mode_starts_without_http_signing_secret(self):
        settings = SimpleNamespace(
            slack_bot_token="xoxb-test",
            slack_app_token="xapp-test",
            slack_signing_secret="",
        )

        def build_app(**kwargs):
            if kwargs.get("request_verification_enabled", True):
                raise ValueError("signing_secret must not be empty")
            return _FakeBoltApp()

        service = SlackSocketService()
        with (
            patch("backend.slack_socket.get_settings", return_value=settings),
            patch("backend.slack_socket.App", side_effect=build_app),
            patch("backend.slack_socket.SocketModeHandler", _FakeSocketModeHandler),
            patch("backend.slack_socket.threading.Thread", _FakeThread),
        ):
            service.start()

        self.assertTrue(service._started)


if __name__ == "__main__":
    unittest.main()
