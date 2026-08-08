from contextlib import asynccontextmanager
import importlib
import os
import sqlite3
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import sleep
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from backend.config import Settings
from backend.database import ensure_notes_expiry_schema_sync


SERVER_NAMES = (
    "atlas",
    "borealis",
    "ceres",
    "daedalus",
    "echo",
    "forge",
    "gaia",
    "helios",
    "io",
)


def import_live_database(test_case: unittest.TestCase):
    try:
        return importlib.import_module("backend.live_database")
    except ImportError as exc:
        test_case.fail(f"backend.live_database import failed: {exc}")


def create_legacy_live_db(path: Path, *, server_names=SERVER_NAMES) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER DEFAULT 22,
                ssh_user TEXT NOT NULL,
                network TEXT DEFAULT 'internal',
                display_order INTEGER DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME
            )
            """
        )
        for index, name in enumerate(server_names):
            conn.execute(
                """
                INSERT INTO servers (name, host, port, ssh_user, network, display_order, created_at)
                VALUES (?, ?, 22, 'monitor', 'internal', ?, '2026-08-08 00:00:00')
                """,
                (name, f"10.0.0.{index + 1}", index),
            )
        conn.execute(
            """
            INSERT INTO notes (server_id, username, content, created_at)
            VALUES (1, 'legacy-operator', 'legacy note', '2026-08-08 00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def import_backend_main_for_env(env: dict[str, str]):
    module_names = (
        "backend.main",
        "backend.database",
        "backend.config",
        "backend.live_database",
    )
    previous_values = {key: os.environ.get(key) for key in env}

    existing_database_module = sys.modules.get("backend.database")
    if existing_database_module is not None and hasattr(existing_database_module, "engine"):
        await existing_database_module.engine.dispose()

    for key, value in env.items():
        os.environ[key] = value

    for module_name in module_names:
        sys.modules.pop(module_name, None)

    try:
        main_module = importlib.import_module("backend.main")
        live_database_module = importlib.import_module("backend.live_database")
        yield main_module, live_database_module
    finally:
        reloaded_database_module = sys.modules.get("backend.database")
        if reloaded_database_module is not None and hasattr(reloaded_database_module, "engine"):
            await reloaded_database_module.engine.dispose()

        for module_name in module_names:
            sys.modules.pop(module_name, None)

        for key, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


class LiveDatabasePathTests(unittest.TestCase):
    def test_sqlite_path_from_url_decodes_absolute_file_uri_and_strips_query(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory(prefix="gpu monitor ") as tmpdir:
            db_path = Path(tmpdir) / "state dir" / "live.db"
            encoded = str(db_path).replace(" ", "%20")

            resolved = module.sqlite_path_from_url(f"sqlite+aiosqlite:///{encoded}?cache=shared")

        self.assertEqual(resolved, db_path)

    def test_sqlite_path_from_url_rejects_in_memory_database(self) -> None:
        module = import_live_database(self)

        with self.assertRaises(module.LiveDatabaseError):
            module.sqlite_path_from_url("sqlite+aiosqlite:///:memory:")


class LiveDatabaseBackupTests(unittest.TestCase):
    def test_backup_live_database_creates_private_copy_and_retains_newest_files(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "live.db"
            backup_dir = root / "backups"
            create_legacy_live_db(source)

            first = module.backup_live_database(source, backup_dir, 2)
            first_mode = first.stat().st_mode & 0o777
            self.assertEqual(first_mode, 0o600)

            sleep(0.01)
            with sqlite3.connect(source) as conn:
                conn.execute(
                    "INSERT INTO notes (server_id, username, content, created_at) VALUES (1, 'second', 'note', '2026-08-08 00:00:00')"
                )
                conn.commit()
            second = module.backup_live_database(source, backup_dir, 2)

            sleep(0.01)
            with sqlite3.connect(source) as conn:
                conn.execute(
                    "INSERT INTO notes (server_id, username, content, created_at) VALUES (1, 'third', 'note', '2026-08-08 00:00:00')"
                )
                conn.commit()
            third = module.backup_live_database(source, backup_dir, 2)

            backups = sorted(backup_dir.glob("gpu-monitor-*.db"))
            self.assertEqual(backups, [second, third])
            self.assertFalse(first.exists())

            with sqlite3.connect(third) as conn:
                note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            self.assertEqual(note_count, 3)


class LiveDatabasePrepareTests(unittest.TestCase):
    def test_prepare_live_database_accepts_legacy_schema_backs_up_database_and_preserves_rows(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "gpu-monitor.db"
            backup_dir = root / "backups"
            create_legacy_live_db(db_path)

            snapshot = module.prepare_live_database(
                f"sqlite+aiosqlite:///{db_path}",
                9,
                str(backup_dir),
                3,
            )

            self.assertEqual(snapshot.server_count, 9)
            self.assertEqual(snapshot.server_names, SERVER_NAMES)
            self.assertEqual(snapshot.note_count, 1)
            self.assertTrue(snapshot.integrity_ok)
            self.assertTrue(next(backup_dir.glob("gpu-monitor-*.db"), None))

            engine = create_engine(f"sqlite:///{db_path}")
            try:
                with engine.begin() as conn:
                    ensure_notes_expiry_schema_sync(conn)
                    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(notes)")).fetchall()}
                    server_count = conn.execute(text("SELECT COUNT(*) FROM servers")).scalar_one()
                    note_count = conn.execute(text("SELECT COUNT(*) FROM notes")).scalar_one()

                self.assertTrue(
                    {"display_name", "priority", "kind", "gpu_indices", "expires_at"}.issubset(columns)
                )
                self.assertEqual(server_count, 9)
                self.assertEqual(note_count, 1)
            finally:
                engine.dispose()

    def test_prepare_live_database_rejects_missing_database_before_schema_mutation(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing_path = root / "missing.db"

            with self.assertRaisesRegex(module.LiveDatabaseError, "missing"):
                module.prepare_live_database(
                    f"sqlite+aiosqlite:///{missing_path}",
                    9,
                    str(root / "backups"),
                    3,
                )

            self.assertFalse(missing_path.exists())

    def test_prepare_live_database_rejects_non_sqlite_urls_when_expected_count_is_set(self) -> None:
        module = import_live_database(self)

        with self.assertRaisesRegex(module.LiveDatabaseError, "SQLite"):
            module.prepare_live_database(
                "postgresql+psycopg://monitor:pw@localhost/gpu_monitor",
                9,
                "/tmp/backups",
                3,
            )

    def test_prepare_live_database_rejects_corrupt_database_before_schema_mutation(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "corrupt.db"
            db_path.write_bytes(b"not a sqlite database")

            with self.assertRaisesRegex(module.LiveDatabaseError, "corrupt"):
                module.prepare_live_database(
                    f"sqlite+aiosqlite:///{db_path}",
                    9,
                    str(root / "backups"),
                    3,
                )

            self.assertEqual(db_path.read_bytes(), b"not a sqlite database")

    def test_prepare_live_database_rejects_zero_registered_servers_before_schema_mutation(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "zero.db"
            create_legacy_live_db(db_path, server_names=())

            with self.assertRaisesRegex(module.LiveDatabaseError, "0 registered"):
                module.prepare_live_database(
                    f"sqlite+aiosqlite:///{db_path}",
                    9,
                    str(root / "backups"),
                    3,
                )

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
                server_count = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
            self.assertEqual(server_count, 0)
            self.assertNotIn("priority", columns)
            self.assertNotIn("display_name", columns)

    def test_prepare_live_database_rejects_count_mismatch_before_schema_mutation(self) -> None:
        module = import_live_database(self)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "mismatch.db"
            create_legacy_live_db(db_path, server_names=SERVER_NAMES[:8])

            with self.assertRaisesRegex(module.LiveDatabaseError, "expected 9"):
                module.prepare_live_database(
                    f"sqlite+aiosqlite:///{db_path}",
                    9,
                    str(root / "backups"),
                    3,
                )

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
                server_count = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
            self.assertEqual(server_count, 8)
            self.assertNotIn("priority", columns)
            self.assertNotIn("display_name", columns)


class LiveDatabaseLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_missing_database_fails_before_init_db_creates_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "missing.db"
            backup_dir = root / "backups"

            env = {
                "SECRET_KEY": "0123456789abcdef0123456789abcdef",
                "ADMIN_PASSWORD": "safe-admin-password",
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
                "MONITORING_EXPECTED_SERVER_COUNT": "9",
                "MONITORING_DATABASE_BACKUP_DIR": str(backup_dir),
                "MONITORING_DATABASE_BACKUP_KEEP": "1",
                "MONITORING_DISABLE_COLLECTORS": "true",
                "MONITORING_DISABLE_SLACK": "true",
            }

            async with import_backend_main_for_env(env) as (main_module, live_database_module):
                with self.assertRaisesRegex(live_database_module.LiveDatabaseError, "missing"):
                    async with main_module.app.router.lifespan_context(main_module.app):
                        pass

            self.assertFalse(db_path.exists())
            self.assertFalse(backup_dir.exists())

    async def test_lifespan_legacy_database_backs_up_before_migration_and_preserves_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "gpu-monitor.db"
            backup_dir = root / "backups"
            create_legacy_live_db(db_path)

            env = {
                "SECRET_KEY": "0123456789abcdef0123456789abcdef",
                "ADMIN_PASSWORD": "safe-admin-password",
                "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
                "MONITORING_EXPECTED_SERVER_COUNT": "9",
                "MONITORING_DATABASE_BACKUP_DIR": str(backup_dir),
                "MONITORING_DATABASE_BACKUP_KEEP": "1",
                "MONITORING_DISABLE_COLLECTORS": "true",
                "MONITORING_DISABLE_SLACK": "true",
            }

            async with import_backend_main_for_env(env) as (main_module, _):
                async with main_module.app.router.lifespan_context(main_module.app):
                    pass

            backups = sorted(backup_dir.glob("gpu-monitor-*.db"))
            self.assertEqual(len(backups), 1)

            with sqlite3.connect(backups[0]) as backup_conn:
                backup_columns = {row[1] for row in backup_conn.execute("PRAGMA table_info(notes)").fetchall()}
                backup_server_count = backup_conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
                backup_note_count = backup_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

            self.assertEqual(backup_server_count, 9)
            self.assertEqual(backup_note_count, 1)
            self.assertNotIn("priority", backup_columns)
            self.assertNotIn("display_name", backup_columns)
            self.assertNotIn("kind", backup_columns)
            self.assertNotIn("gpu_indices", backup_columns)
            self.assertNotIn("expires_at", backup_columns)

            with sqlite3.connect(db_path) as live_conn:
                live_columns = {row[1] for row in live_conn.execute("PRAGMA table_info(notes)").fetchall()}
                live_server_count = live_conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
                live_note_count = live_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

            self.assertEqual(live_server_count, 9)
            self.assertEqual(live_note_count, 1)
            self.assertTrue(
                {"display_name", "priority", "kind", "gpu_indices", "expires_at"}.issubset(live_columns)
            )


class LiveDatabaseSettingsTests(unittest.TestCase):
    def test_settings_reject_negative_existing_integer_fields(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                secret_key="0123456789abcdef0123456789abcdef",
                admin_password="safe-admin-password",
                collect_interval=-1,
            )

    def test_settings_reject_negative_monitoring_expected_server_count(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                secret_key="0123456789abcdef0123456789abcdef",
                admin_password="safe-admin-password",
                monitoring_expected_server_count=-1,
            )

    def test_settings_require_positive_backup_keep_when_backup_dir_is_configured(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                secret_key="0123456789abcdef0123456789abcdef",
                admin_password="safe-admin-password",
                monitoring_database_backup_dir="/tmp/backups",
                monitoring_database_backup_keep=0,
            )


if __name__ == "__main__":
    unittest.main()
