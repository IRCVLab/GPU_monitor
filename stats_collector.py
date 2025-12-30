import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Database Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- GPU-level metrics over time
CREATE TABLE IF NOT EXISTS gpu_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    server_alias TEXT NOT NULL,
    gpu_id INTEGER NOT NULL,
    gpu_name TEXT,

    -- Memory metrics (in MB)
    memory_used REAL,
    memory_total REAL,
    memory_percent REAL,

    -- Utilization (percentage)
    utilization_gpu REAL,
    utilization_memory REAL,

    -- Other metrics
    temperature REAL,
    power_draw REAL
);

CREATE INDEX IF NOT EXISTS idx_gpu_metrics_timestamp ON gpu_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_gpu_metrics_server_time ON gpu_metrics(server_alias, timestamp);
CREATE INDEX IF NOT EXISTS idx_gpu_metrics_gpu_time ON gpu_metrics(server_alias, gpu_id, timestamp);

-- User activity tracking
CREATE TABLE IF NOT EXISTS gpu_process_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    server_alias TEXT NOT NULL,
    gpu_id INTEGER NOT NULL,

    -- User information
    username TEXT NOT NULL,
    pid INTEGER,
    command TEXT,
    memory_used REAL
);

CREATE INDEX IF NOT EXISTS idx_process_user_time ON gpu_process_metrics(username, timestamp);
CREATE INDEX IF NOT EXISTS idx_process_server_user_time ON gpu_process_metrics(server_alias, username, timestamp);
CREATE INDEX IF NOT EXISTS idx_process_gpu_user ON gpu_process_metrics(server_alias, gpu_id, username, timestamp);

-- Pre-aggregated server-level data
CREATE TABLE IF NOT EXISTS server_summary_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    server_alias TEXT NOT NULL,

    -- Aggregated GPU metrics
    gpu_count INTEGER,
    total_memory REAL,
    total_memory_used REAL,
    avg_utilization REAL,
    avg_temperature REAL,

    -- Server status
    status TEXT
);

CREATE INDEX IF NOT EXISTS idx_summary_server_time ON server_summary_metrics(server_alias, timestamp);

-- Collection health tracking
CREATE TABLE IF NOT EXISTS collection_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    collection_duration_ms REAL,
    servers_collected INTEGER,
    errors TEXT
);

CREATE INDEX IF NOT EXISTS idx_collection_time ON collection_metadata(timestamp);
"""


# ─── DatabaseManager Class ────────────────────────────────────────────────────

class DatabaseManager:
    """Thread-safe SQLite database manager for GPU statistics."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._init_schema()
        logger.info(f"DatabaseManager initialized at {self.db_path}")

    def _init_schema(self):
        """Initialize database schema if not exists."""
        with self.get_connection() as conn:
            try:
                # Check if tables exist
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                existing_tables = {row[0] for row in cursor.fetchall()}

                if not existing_tables or 'gpu_metrics' not in existing_tables:
                    logger.info("Initializing statistics database schema...")
                    conn.executescript(SCHEMA_SQL)
                    conn.commit()
                    logger.info("Database schema created successfully")
                else:
                    logger.info("Database schema already exists")

                # Enable WAL mode for better concurrent access
                conn.execute("PRAGMA journal_mode=WAL")
                conn.commit()

            except Exception as e:
                logger.error(f"Failed to initialize database schema: {e}")
                raise

    @contextmanager
    def get_connection(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_gpu_metrics(self, metrics_batch: List[Dict[str, Any]]):
        """Bulk insert GPU metrics."""
        if not metrics_batch:
            return

        with self.lock:
            with self.get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO gpu_metrics
                    (timestamp, server_alias, gpu_id, gpu_name, memory_used, memory_total,
                     memory_percent, utilization_gpu, utilization_memory, temperature, power_draw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            m['timestamp'],
                            m['server_alias'],
                            m['gpu_id'],
                            m.get('gpu_name'),
                            m.get('memory_used'),
                            m.get('memory_total'),
                            m.get('memory_percent'),
                            m.get('utilization_gpu'),
                            m.get('utilization_memory'),
                            m.get('temperature'),
                            m.get('power_draw'),
                        )
                        for m in metrics_batch
                    ],
                )
                conn.commit()

    def insert_process_metrics(self, metrics_batch: List[Dict[str, Any]]):
        """Bulk insert GPU process metrics."""
        if not metrics_batch:
            return

        with self.lock:
            with self.get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO gpu_process_metrics
                    (timestamp, server_alias, gpu_id, username, pid, command, memory_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            m['timestamp'],
                            m['server_alias'],
                            m['gpu_id'],
                            m['username'],
                            m.get('pid'),
                            m.get('command'),
                            m.get('memory_used'),
                        )
                        for m in metrics_batch
                    ],
                )
                conn.commit()

    def insert_server_summary(self, summary_batch: List[Dict[str, Any]]):
        """Bulk insert server summary metrics."""
        if not summary_batch:
            return

        with self.lock:
            with self.get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO server_summary_metrics
                    (timestamp, server_alias, gpu_count, total_memory, total_memory_used,
                     avg_utilization, avg_temperature, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            m['timestamp'],
                            m['server_alias'],
                            m['gpu_count'],
                            m['total_memory'],
                            m['total_memory_used'],
                            m['avg_utilization'],
                            m.get('avg_temperature'),
                            m['status'],
                        )
                        for m in summary_batch
                    ],
                )
                conn.commit()

    def insert_collection_metadata(self, metadata: Dict[str, Any]):
        """Insert collection metadata."""
        with self.lock:
            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO collection_metadata
                    (timestamp, collection_duration_ms, servers_collected, errors)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        metadata['timestamp'],
                        metadata['collection_duration_ms'],
                        metadata['servers_collected'],
                        metadata.get('errors'),
                    ),
                )
                conn.commit()

    def get_db_size(self) -> int:
        """Get database file size in bytes."""
        try:
            return self.db_path.stat().st_size
        except Exception:
            return 0

    def get_record_counts(self) -> Dict[str, int]:
        """Get record counts for all tables."""
        counts = {}
        with self.get_connection() as conn:
            for table in ['gpu_metrics', 'gpu_process_metrics', 'server_summary_metrics', 'collection_metadata']:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
        return counts


# ─── StatsCollector Class ─────────────────────────────────────────────────────

class StatsCollector:
    """Background thread that collects GPU statistics periodically."""

    def __init__(self, db_path: str, monitors: Dict[str, Any], interval: int = 60):
        """
        Initialize stats collector.

        Args:
            db_path: Path to SQLite database
            monitors: Dict of {alias: ServerMonitor} from monitor.py
            interval: Collection interval in seconds (default: 60)
        """
        self.db = DatabaseManager(db_path)
        self.monitors = monitors
        self.interval = interval
        self.running = False
        self.thread: Optional[threading.Thread] = None
        logger.info(f"StatsCollector initialized with {len(monitors)} monitors, interval={interval}s")

    def start(self):
        """Start the background collection thread."""
        if self.running:
            logger.warning("StatsCollector already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="StatsCollector")
        self.thread.start()
        logger.info("StatsCollector thread started")

    def stop(self):
        """Stop the background collection thread."""
        if not self.running:
            return

        logger.info("Stopping StatsCollector...")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("StatsCollector stopped")

    def _run(self):
        """Main collection loop."""
        while self.running:
            try:
                self._collect_once()
            except Exception as e:
                logger.error(f"Collection error: {e}", exc_info=True)

            # Sleep with interrupt check
            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)

    def _collect_once(self):
        """Perform one collection cycle."""
        start_time = time.time()
        timestamp = datetime.now(timezone.utc).isoformat()

        gpu_metrics = []
        process_metrics = []
        summary_metrics = []
        errors = []
        servers_collected = 0

        for alias, monitor in self.monitors.items():
            try:
                snapshot = monitor.get_data()
                if not snapshot:
                    continue

                resources = snapshot.get('resources', {})
                gpu_data = resources.get('gpu')

                if not gpu_data:
                    continue

                # Extract per-GPU metrics
                gpus = gpu_data.get('gpus', [])
                for gpu in gpus:
                    memory = gpu.get('memory', {}) or {}
                    utilization = gpu.get('utilization', {}) or {}

                    gpu_metrics.append({
                        'timestamp': timestamp,
                        'server_alias': alias,
                        'gpu_id': gpu.get('id', 0),
                        'gpu_name': gpu.get('name'),
                        'memory_used': memory.get('used'),
                        'memory_total': memory.get('total'),
                        'memory_percent': memory.get('percent'),
                        'utilization_gpu': utilization.get('gpu'),
                        'utilization_memory': utilization.get('memory'),
                        'temperature': gpu.get('temperature'),
                        'power_draw': gpu.get('power'),
                    })

                    # Extract process metrics (user activity)
                    for proc in gpu.get('processes', []):
                        username = proc.get('username')
                        if not username:
                            continue

                        process_metrics.append({
                            'timestamp': timestamp,
                            'server_alias': alias,
                            'gpu_id': gpu.get('id', 0),
                            'username': username,
                            'pid': proc.get('pid'),
                            'command': (proc.get('command') or '')[:200],  # Truncate
                            'memory_used': proc.get('memory_used'),
                        })

                # Extract server summary
                summary = gpu_data.get('summary', {})
                summary_metrics.append({
                    'timestamp': timestamp,
                    'server_alias': alias,
                    'gpu_count': summary.get('count', 0),
                    'total_memory': summary.get('total_memory', 0),
                    'total_memory_used': summary.get('total_memory_used', 0),
                    'avg_utilization': summary.get('avg_utilization', 0),
                    'avg_temperature': None,  # Calculate if needed
                    'status': snapshot.get('status', 'unknown'),
                })

                servers_collected += 1

            except Exception as e:
                error_msg = f"{alias}: {str(e)}"
                errors.append(error_msg)
                logger.error(f"Error collecting from {alias}: {e}")

        # Insert all metrics in bulk
        try:
            self.db.insert_gpu_metrics(gpu_metrics)
            self.db.insert_process_metrics(process_metrics)
            self.db.insert_server_summary(summary_metrics)

            # Record collection metadata
            duration_ms = (time.time() - start_time) * 1000
            self.db.insert_collection_metadata({
                'timestamp': timestamp,
                'collection_duration_ms': duration_ms,
                'servers_collected': servers_collected,
                'errors': json.dumps(errors) if errors else None,
            })

            logger.info(
                f"Collection complete: {servers_collected} servers, "
                f"{len(gpu_metrics)} GPU metrics, {len(process_metrics)} process metrics, "
                f"{duration_ms:.1f}ms"
            )

        except Exception as e:
            logger.error(f"Failed to insert metrics: {e}", exc_info=True)
