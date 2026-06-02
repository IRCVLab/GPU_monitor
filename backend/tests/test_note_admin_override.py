from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.routers.notes import _matches_admin_password


class NoteAdminOverrideTests(TestCase):
    def test_matches_admin_password_returns_true_for_admin_secret(self) -> None:
        with patch("backend.routers.notes.get_settings", return_value=SimpleNamespace(admin_password="ircv_admin")):
            self.assertTrue(_matches_admin_password("ircv_admin"))

    def test_matches_admin_password_returns_false_for_other_secret(self) -> None:
        with patch("backend.routers.notes.get_settings", return_value=SimpleNamespace(admin_password="ircv_admin")):
            self.assertFalse(_matches_admin_password("123123"))

    def test_matches_admin_password_returns_false_for_missing_secret(self) -> None:
        with patch("backend.routers.notes.get_settings", return_value=SimpleNamespace(admin_password="ircv_admin")):
            self.assertFalse(_matches_admin_password(None))
