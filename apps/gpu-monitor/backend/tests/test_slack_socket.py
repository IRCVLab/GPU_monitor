import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.slack_socket import SlackSocketService, storage_query_for_command


class _FakeBoltApp:
    commands = []
    handlers = {}

    def command(self, _name):
        self.commands.append(_name)

        def decorator(func):
            self.handlers[_name] = func
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
    def setUp(self):
        _FakeBoltApp.commands = []
        _FakeBoltApp.handlers = {}

    def _start_service(self):
        settings = SimpleNamespace(
            slack_bot_token="xoxb-test",
            slack_app_token="xapp-test",
            slack_signing_secret="",
            storage_monitor_api_url="http://127.0.0.1:8088/api/servers",
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
        return service

    def test_socket_mode_starts_without_http_signing_secret(self):
        service = self._start_service()

        self.assertTrue(service._started)
        self.assertIn("/gpu", _FakeBoltApp.commands)
        self.assertIn("/status", _FakeBoltApp.commands)
        self.assertIn("/storage", _FakeBoltApp.commands)

    def test_storage_command_and_existing_command_aliases_extract_query(self):
        self.assertEqual(
            storage_query_for_command({"command": "/storage", "text": "poseidon"}),
            "poseidon",
        )
        self.assertEqual(
            storage_query_for_command({"command": "/gpu", "text": "storage poseidon"}),
            "poseidon",
        )
        self.assertEqual(
            storage_query_for_command({"command": "/status", "text": "storage"}),
            "",
        )
        self.assertIsNone(
            storage_query_for_command({"command": "/gpu", "text": "hinton"})
        )

    def test_storage_handler_fetches_builds_and_responds(self):
        self._start_service()
        ack = Mock()
        respond = Mock()
        summary = {"servers": []}
        payload = {"response_type": "ephemeral", "text": "storage"}

        with (
            patch("backend.slack_socket.fetch_storage_summary", return_value=summary) as fetch,
            patch("backend.slack_socket.build_storage_command_payload", return_value=payload) as build,
        ):
            _FakeBoltApp.handlers["/storage"](
                ack,
                {"command": "/storage", "text": " poseidon "},
                respond,
            )

        ack.assert_called_once_with()
        fetch.assert_called_once_with("http://127.0.0.1:8088/api/servers")
        build.assert_called_once_with(summary, "poseidon")
        respond.assert_called_once_with(**payload)

    def test_gpu_storage_alias_uses_storage_handler_path(self):
        self._start_service()
        ack = Mock()
        respond = Mock()
        summary = {"servers": []}
        payload = {"response_type": "ephemeral", "text": "storage"}

        with (
            patch("backend.slack_socket.fetch_storage_summary", return_value=summary),
            patch("backend.slack_socket.build_storage_command_payload", return_value=payload) as build,
        ):
            _FakeBoltApp.handlers["/gpu"](
                ack,
                {"command": "/gpu", "text": "storage hinton"},
                respond,
            )

        build.assert_called_once_with(summary, "hinton")
        respond.assert_called_once_with(**payload)

    def test_status_storage_alias_uses_storage_handler_path(self):
        self._start_service()
        respond = Mock()
        summary = {"servers": []}
        payload = {"response_type": "ephemeral", "text": "storage"}

        with (
            patch("backend.slack_socket.fetch_storage_summary", return_value=summary),
            patch("backend.slack_socket.build_storage_command_payload", return_value=payload) as build,
        ):
            _FakeBoltApp.handlers["/status"](
                Mock(),
                {"command": "/status", "text": "storage issues"},
                respond,
            )

        build.assert_called_once_with(summary, "issues")
        respond.assert_called_once_with(**payload)

    def test_storage_handler_returns_bounded_error_response(self):
        self._start_service()
        ack = Mock()
        respond = Mock()

        with patch(
            "backend.slack_socket.fetch_storage_summary",
            side_effect=RuntimeError("offline"),
        ):
            with self.assertLogs("backend.slack_socket", level="ERROR"):
                _FakeBoltApp.handlers["/storage"](
                    ack,
                    {"command": "/storage", "text": ""},
                    respond,
                )

        respond.assert_called_once_with(
            response_type="ephemeral",
            text="Storage Monitor 응답을 불러오지 못했습니다.",
        )


if __name__ == "__main__":
    unittest.main()
