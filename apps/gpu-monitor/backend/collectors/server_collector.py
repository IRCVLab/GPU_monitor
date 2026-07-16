"""Per-server collection loop running as an asyncio task."""
import asyncio
import json
import logging
import socket
from datetime import datetime, timedelta, timezone

import paramiko
from sqlalchemy import delete, select

try:
    from ..config import get_settings
    from ..database import AsyncSessionLocal
    from ..event_logger import get_event_log_health, log_event
    from ..models import GpuMetric, Server
    from .gpu import parse_gpustat, parse_nvidia_smi, NVIDIA_SMI_CMD
    from .gpu_health import GpuInventoryTracker
    from .ssh_client import SSHClient
    from .storage import StorageCollector
    from .system import SYSTEM_CMD_PROC, SYSTEM_CMD_PSUTIL, calculate_disk_io_rate, parse_system
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import AsyncSessionLocal
    from event_logger import get_event_log_health, log_event
    from models import GpuMetric, Server
    from collectors.gpu import parse_gpustat, parse_nvidia_smi, NVIDIA_SMI_CMD
    from collectors.gpu_health import GpuInventoryTracker
    from collectors.ssh_client import SSHClient
    from collectors.storage import StorageCollector
    from collectors.system import SYSTEM_CMD_PROC, SYSTEM_CMD_PSUTIL, calculate_disk_io_rate, parse_system

logger = logging.getLogger(__name__)

_RECONNECT_CYCLE = 360   # 360 × 10s = 1h forced reconnect
_OFFLINE_THRESHOLD = 3   # consecutive failures before → offline
_RETRY_SLEEP = 15        # seconds between retries on error
_WARN_MINUTES = 3        # minutes before connection_warning → connection_alert


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ServerCollector:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.status: str = "unknown"   # "online" | "offline" | "degraded" | "unknown"
        self.last_seen: datetime | None = None
        self.current_data: dict | None = None
        self._fail_count: int = 0
        self._ssh: SSHClient = SSHClient(server)
        self._storage: StorageCollector = StorageCollector()
        self._last_archive: datetime | None = None
        self._last_cleanup: datetime | None = None
        self._reconnect_count: int = 0
        self._offline_since: datetime | None = None
        self._failure_started_at: datetime | None = None
        self._status_reason: dict | None = None
        # connection warning/alert flags — reset on recovery
        self._warn_sent: bool = False
        self._alert_sent: bool = False
        # GPU user tracking for process_start / process_end events
        self._prev_users: dict[int, set[str]] = {}
        self._last_system_info = None
        self._historical_gpu_indices_loaded: bool = False
        self._gpu_inventory_tracker = GpuInventoryTracker()

    @property
    def offline_since(self) -> datetime | None:
        return self._offline_since

    @property
    def status_reason(self) -> dict | None:
        return self._status_reason

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main collection loop — runs forever."""
        settings = get_settings()

        while True:
            try:
                if not self._ssh.is_connected or self._reconnect_count >= _RECONNECT_CYCLE:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._ssh_reconnect
                    )
                    self._reconnect_count = 0

                data, degraded, degraded_reason = await self._collect_once()
                self.current_data = data

                now = _utcnow()
                was_offline = self.status in ("offline", "unknown")
                prev_status = self.status
                recovery_started_at = self._failure_started_at
                self.status = "degraded" if degraded else "online"
                self._status_reason = degraded_reason
                self._fail_count = 0
                self.last_seen = now
                self._reconnect_count += 1

                if recovery_started_at is not None:
                    self._failure_started_at = None
                    self._warn_sent = False
                    self._alert_sent = False

                if was_offline and self._offline_since is not None:
                    downtime = int((now - self._offline_since).total_seconds())
                    self._offline_since = None
                    asyncio.create_task(self._notify_recovery(downtime))
                    asyncio.create_task(log_event(
                        "server_online", "info",
                        f"{self.server.name} came back online (downtime {downtime}s)",
                        server_id=self.server.id,
                        server_name=self.server.name,
                        metadata={"downtime_seconds": downtime},
                    ))
                elif recovery_started_at is not None and prev_status == "degraded":
                    downtime = int((now - recovery_started_at).total_seconds())
                    asyncio.create_task(log_event(
                        "connection_recovered", "info",
                        f"{self.server.name} connection recovered",
                        server_id=self.server.id,
                        server_name=self.server.name,
                        metadata={"downtime_seconds": downtime},
                    ))

                if degraded and prev_status != "degraded":
                    asyncio.create_task(self._notify_degraded())
                    asyncio.create_task(log_event(
                        "server_degraded", "warning",
                        f"{self.server.name} is degraded (GPU or system metrics unavailable)",
                        server_id=self.server.id,
                        server_name=self.server.name,
                    ))

                if self._should_archive():
                    await self._archive(data)

                if self._should_cleanup():
                    await self._cleanup_history()

                await self._broadcast(data)
                await asyncio.sleep(settings.collect_interval)

            except Exception as exc:
                now = _utcnow()
                prev_status = self.status
                should_broadcast = False
                self._fail_count += 1
                self._status_reason = self._normalize_status_reason(exc)
                if self._failure_started_at is None:
                    self._failure_started_at = now
                elapsed = int((now - self._failure_started_at).total_seconds())

                if self.status != "offline":
                    self.status = "degraded"
                if self.status != prev_status:
                    should_broadcast = True

                logger.warning(
                    "Collector error for %s (fail #%d): %s",
                    self.server.name, self._fail_count, exc,
                )

                if not self._warn_sent:
                    self._warn_sent = True
                    asyncio.create_task(log_event(
                        "connection_warning", "warning",
                        f"{self.server.name} connection issue detected",
                        server_id=self.server.id,
                        server_name=self.server.name,
                        metadata={
                            "elapsed_seconds": elapsed,
                            "fail_count": self._fail_count,
                            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
                            "reason": self._status_reason,
                        },
                    ))
                    should_broadcast = True

                if self._fail_count >= _OFFLINE_THRESHOLD and self.status != "offline":
                    self.status = "offline"
                    self._offline_since = self._failure_started_at or now
                    asyncio.create_task(self._notify_offline())
                    asyncio.create_task(log_event(
                        "server_offline", "critical",
                        f"{self.server.name} went offline",
                        server_id=self.server.id,
                        server_name=self.server.name,
                        metadata={
                            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
                            "reason": self._status_reason,
                        },
                    ))
                    should_broadcast = True

                # Emit connection_warning / connection_alert (each once)
                if self._failure_started_at is not None:
                    elapsed = int((now - self._failure_started_at).total_seconds())
                    if elapsed >= _WARN_MINUTES * 60 and not self._alert_sent:
                        self._alert_sent = True
                        asyncio.create_task(self._notify_connection_alert(elapsed))
                        asyncio.create_task(log_event(
                            "connection_alert", "critical",
                            f"{self.server.name} unreachable for {elapsed}s (>= {_WARN_MINUTES}min)",
                            server_id=self.server.id,
                            server_name=self.server.name,
                            metadata={
                                "elapsed_seconds": elapsed,
                                "last_seen": self.last_seen.isoformat() if self.last_seen else None,
                                "reason": self._status_reason,
                            },
                        ))
                        should_broadcast = True

                if should_broadcast:
                    await self._broadcast(self.current_data or {})

                self._ssh.close()
                await asyncio.sleep(_RETRY_SLEEP)

    # ------------------------------------------------------------------
    # Collection helpers
    # ------------------------------------------------------------------

    def _ssh_reconnect(self) -> None:
        """Blocking reconnect — call from executor."""
        self._ssh.close()
        self._ssh.connect()

    def _status_reason_payload(
        self,
        code: str,
        source: str,
        message: str,
        retryable: bool,
    ) -> dict:
        return {
            "code": code,
            "source": source,
            "message": message,
            "retryable": retryable,
            "updated_at": _utcnow().isoformat(),
        }

    def _build_gpu_inventory_degraded_reason(self, inventory: dict) -> dict:
        missing = inventory.get("missing_indices") or []
        if missing:
            detail = f"missing GPU indices: {missing}"
        else:
            detail = (
                f"visible GPU count {inventory.get('visible_count')} is less than "
                f"expected {inventory.get('expected_count')}"
            )
        return self._status_reason_payload(
            "gpu_device_missing",
            "gpu",
            f"GPU inventory mismatch detected ({detail})",
            True,
        )

    def _build_degraded_reason(self, gpu_failed: bool, system_failed: bool) -> dict | None:
        if gpu_failed and system_failed:
            return self._status_reason_payload(
                "collector_error",
                "collector",
                "GPU 및 시스템 메트릭 수집 실패",
                True,
            )
        if gpu_failed:
            return self._status_reason_payload(
                "gpu_collect_failed",
                "gpu",
                "GPU 메트릭 수집 실패",
                True,
            )
        if system_failed:
            return self._status_reason_payload(
                "system_collect_failed",
                "system",
                "시스템 메트릭 수집 실패",
                True,
            )
        return None

    def _normalize_status_reason(self, exc: Exception) -> dict:
        if isinstance(exc, paramiko.AuthenticationException):
            return self._status_reason_payload("auth_failed", "connect", "SSH 인증 실패", False)

        if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
            return self._status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

        if isinstance(exc, (socket.timeout, TimeoutError)):
            return self._status_reason_payload("timeout", "connect", "SSH 연결 시간 초과", True)

        if isinstance(exc, paramiko.SSHException):
            return self._status_reason_payload("ssh_error", "connect", "SSH 연결 오류", True)

        if isinstance(exc, ConnectionRefusedError):
            return self._status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

        message = str(exc)
        if "no ssh credentials configured" in message.lower():
            return self._status_reason_payload("missing_credentials", "connect", "SSH 인증 정보 없음", False)
        if "timed out" in message.lower():
            return self._status_reason_payload("timeout", "connect", "SSH 연결 시간 초과", True)
        if "authentication failed" in message.lower():
            return self._status_reason_payload("auth_failed", "connect", "SSH 인증 실패", False)
        if "refused" in message.lower():
            return self._status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

        return self._status_reason_payload("ssh_error", "collector", "서버 연결 오류", True)

    def _sync_collect_gpu(self) -> "ServerGpuData":
        """Collect GPU data — tries gpustat first, falls back to nvidia-smi."""
        try:
            raw = self._ssh.run("gpustat --json --no-header 2>/dev/null")
            return parse_gpustat(raw)
        except Exception:
            raw = self._ssh.run(NVIDIA_SMI_CMD)
            return parse_nvidia_smi(raw)

    def _sync_collect_system(self) -> str | None:
        """Collect system metrics — /proc first, psutil fallback, None on failure."""
        try:
            return self._ssh.run(SYSTEM_CMD_PROC)
        except Exception:
            pass
        try:
            return self._ssh.run(SYSTEM_CMD_PSUTIL)
        except Exception as exc:
            logger.debug("System metrics unavailable for %s: %s", self.server.name, exc)
            return None

    async def _load_historical_gpu_indices_once(self) -> set[int]:
        if self._historical_gpu_indices_loaded:
            return self._gpu_inventory_tracker.expected_indices

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(GpuMetric.gpu_index)
                    .where(GpuMetric.server_id == self.server.id)
                    .distinct()
                )
                indices = {int(index) for index in result.scalars().all() if index is not None}
                self._gpu_inventory_tracker.add_historical_indices(indices)
        except Exception as exc:
            logger.debug("Historical GPU inventory unavailable for %s: %s", self.server.name, exc)
        finally:
            self._historical_gpu_indices_loaded = True

        return self._gpu_inventory_tracker.expected_indices

    def _sync_collect_storage(self) -> dict | None:
        """Collect storage metrics with a 10-minute TTL cache."""
        try:
            return self._storage.collect(self._ssh).to_dict()
        except Exception as exc:
            logger.debug("Storage metrics unavailable for %s: %s", self.server.name, exc)
            cached = self._storage.get_cached()
            return cached.to_dict() if cached is not None else None

    async def _collect_once(self) -> tuple[dict, bool, dict | None]:
        """Collect GPU + system metrics.

        Returns (data_dict, degraded, degraded_reason).
        degraded=True means SSH is alive but GPU or system metrics failed.
        Raises on SSH-level failure (triggers offline logic in run()).
        """
        loop = asyncio.get_event_loop()

        gpu_data = None
        gpu_failed = False
        try:
            gpu_data = await loop.run_in_executor(None, self._sync_collect_gpu)
        except Exception as exc:
            if not self._ssh.is_connected:
                raise  # SSH dead — propagate to run() → offline
            logger.debug("GPU collection failed for %s (SSH ok): %s", self.server.name, exc)
            gpu_failed = True

        system_raw = await loop.run_in_executor(None, self._sync_collect_system)
        system_info = parse_system(system_raw) if system_raw else None
        storage_info = await loop.run_in_executor(None, self._sync_collect_storage)

        degraded = gpu_failed or system_raw is None
        degraded_reason = self._build_degraded_reason(gpu_failed, system_raw is None)
        gpu_inventory = None

        if gpu_data is not None:
            await self._load_historical_gpu_indices_once()
            inventory_health = self._gpu_inventory_tracker.assess(
                visible_indices=[gpu.index for gpu in gpu_data.gpus],
                pci_count=system_info.pci_gpu_count if system_info is not None else None,
            )
            gpu_inventory = inventory_health.to_dict()
            if inventory_health.state == "missing":
                degraded = True
                degraded_reason = self._build_gpu_inventory_degraded_reason(gpu_inventory)

        disk_rate = calculate_disk_io_rate(self._last_system_info, system_info)
        disk_sample_seconds = None
        if (
            self._last_system_info is not None
            and system_info is not None
            and self._last_system_info.disk_sample_time is not None
            and system_info.disk_sample_time is not None
        ):
            elapsed = system_info.disk_sample_time - self._last_system_info.disk_sample_time
            if elapsed > 0:
                disk_sample_seconds = elapsed
        if system_info is not None:
            self._last_system_info = system_info

        gpus_list = (
            [
                {
                    "index": g.index,
                    "name": g.name,
                    "utilization": g.utilization,
                    "memory_used": g.memory_used,
                    "memory_total": g.memory_total,
                    "temperature": g.temperature,
                    "power_draw": g.power_draw,
                    "users": g.users,
                }
                for g in gpu_data.gpus
            ]
            if gpu_data is not None
            else []
        )

        # Detect GPU user changes for process_start / process_end events
        if gpu_data is not None:
            curr_users: dict[int, set[str]] = {}
            started_by_user: dict[str, list[int]] = {}
            ended_by_user: dict[str, list[int]] = {}
            for gpu in gpu_data.gpus:
                curr_users[gpu.index] = set(gpu.users)
                added = curr_users[gpu.index] - self._prev_users.get(gpu.index, set())
                removed = self._prev_users.get(gpu.index, set()) - curr_users[gpu.index]
                for user in added:
                    started_by_user.setdefault(user, []).append(gpu.index)
                for user in removed:
                    ended_by_user.setdefault(user, []).append(gpu.index)

            for user, gpu_indices in started_by_user.items():
                ordered = sorted(gpu_indices)
                gpu_label = ",".join(str(index) for index in ordered)
                scope = "GPUs" if len(ordered) > 1 else "GPU"
                asyncio.create_task(log_event(
                    "process_start", "info",
                    f"{user} started on {scope} {gpu_label}",
                    server_id=self.server.id,
                    server_name=self.server.name,
                    metadata={
                        "user": user,
                        "gpu_indices": ordered,
                        "gpu_count": len(ordered),
                    },
                ))

            for user, gpu_indices in ended_by_user.items():
                ordered = sorted(gpu_indices)
                gpu_label = ",".join(str(index) for index in ordered)
                scope = "GPUs" if len(ordered) > 1 else "GPU"
                asyncio.create_task(log_event(
                    "process_end", "info",
                    f"{user} ended on {scope} {gpu_label}",
                    server_id=self.server.id,
                    server_name=self.server.name,
                    metadata={
                        "user": user,
                        "gpu_indices": ordered,
                        "gpu_count": len(ordered),
                    },
                ))
            self._prev_users = curr_users

        collected_at = gpu_data.collected_at.isoformat() if gpu_data else _utcnow().isoformat()

        data: dict = {
            "server_id": self.server.id,
            "server_name": self.server.name,
            "collected_at": collected_at,
            "gpus": gpus_list,
            "system": (
                {
                    "cpu_percent": system_info.cpu_percent,
                    "ram_used": system_info.ram_used,
                    "ram_total": system_info.ram_total,
                    "io_pressure_some": system_info.io_pressure_some,
                    "io_pressure_full": system_info.io_pressure_full,
                    "io_blocked_tasks": system_info.io_blocked_tasks,
                    "io_pressure_supported": system_info.io_pressure_supported,
                    "cpu_pressure_some": system_info.cpu_pressure_some,
                    "cpu_running_tasks": system_info.cpu_running_tasks,
                    "load_avg_1": system_info.load_avg_1,
                    "load_avg_5": system_info.load_avg_5,
                    "load_avg_15": system_info.load_avg_15,
                    "cpu_count": system_info.cpu_count,
                    "disk_read_bytes_per_second": (
                        disk_rate.read_bytes_per_second if disk_rate is not None else None
                    ),
                    "disk_write_bytes_per_second": (
                        disk_rate.write_bytes_per_second if disk_rate is not None else None
                    ),
                    "disk_sample_seconds": disk_sample_seconds if disk_rate is not None else None,
                }
                if system_info
                else None
            ),
            "storage": storage_info,
            "gpu_inventory": gpu_inventory,
        }
        return data, degraded, degraded_reason

    # ------------------------------------------------------------------
    # Archive & history cleanup
    # ------------------------------------------------------------------

    def _should_archive(self) -> bool:
        settings = get_settings()
        if self._last_archive is None:
            return True
        return (_utcnow() - self._last_archive).total_seconds() >= settings.archive_interval

    def _should_cleanup(self) -> bool:
        """Run cleanup once per day per collector."""
        if self._last_cleanup is None:
            return True
        return (_utcnow() - self._last_cleanup).total_seconds() >= 86400

    async def _archive(self, data: dict) -> None:
        """Persist GPU metrics to DB."""
        try:
            async with AsyncSessionLocal() as session:
                for gpu in data.get("gpus", []):
                    metric = GpuMetric(
                        server_id=self.server.id,
                        gpu_index=gpu["index"],
                        utilization=gpu["utilization"],
                        memory_used=gpu["memory_used"],
                        memory_total=gpu["memory_total"],
                        temperature=gpu["temperature"],
                        power_draw=gpu["power_draw"],
                        active_users=json.dumps(gpu["users"]),
                        collected_at=datetime.utcnow(),
                    )
                    session.add(metric)
                await session.commit()
            self._last_archive = _utcnow()
        except Exception as exc:
            logger.error("Archive failed for %s: %s", self.server.name, exc)

    async def _cleanup_history(self) -> None:
        """Delete gpu_metrics older than history_days."""
        try:
            cutoff = datetime.utcnow() - timedelta(days=get_settings().history_days)
            async with AsyncSessionLocal() as session:
                await session.execute(
                    delete(GpuMetric).where(
                        GpuMetric.server_id == self.server.id,
                        GpuMetric.collected_at < cutoff,
                    )
                )
                await session.commit()
            self._last_cleanup = _utcnow()
            logger.debug("History cleanup done for %s (cutoff=%s)", self.server.name, cutoff.date())
        except Exception as exc:
            logger.error("History cleanup failed for %s: %s", self.server.name, exc)

    # ------------------------------------------------------------------
    # WebSocket broadcast
    # ------------------------------------------------------------------

    async def _broadcast(self, data: dict) -> None:
        try:
            try:
                from ..ws_manager import ws_manager  # lazy import
            except ImportError:  # pragma: no cover - direct execution fallback
                from ws_manager import ws_manager
            payload = {
                "type": "update",
                "data": {
                    "server_id": self.server.id,
                    "server_name": data.get("server_name", self.server.name),
                    "host": self.server.host,
                    "port": self.server.port,
                    "network": self.server.network,
                    "display_order": self.server.display_order,
                    "status": self.status,
                    "status_reason": self._status_reason,
                    "last_seen": self.last_seen.isoformat() if self.last_seen else None,
                    "offline_since": (
                        self._offline_since.isoformat() if self._offline_since else None
                    ),
                    "gpus": data.get("gpus", []),
                    "system": data.get("system"),
                    "storage": data.get("storage"),
                    "gpu_inventory": data.get("gpu_inventory"),
                    "event_log_health": get_event_log_health(),
                },
            }
            await ws_manager.broadcast(payload)
        except Exception as exc:
            logger.debug("Broadcast failed: %s", exc)

    # ------------------------------------------------------------------
    # Slack notifications
    # ------------------------------------------------------------------

    async def _notify_offline(self) -> None:
        try:
            try:
                from ..slack_client import notify_offline
            except ImportError:  # pragma: no cover - direct execution fallback
                from slack_client import notify_offline
            detected_at_str = self._offline_since.isoformat() if self._offline_since else None
            last_seen_str = self.last_seen.isoformat() if self.last_seen else None
            await notify_offline(
                self.server.name,
                self.server.id,
                self.server.host,
                self.server.port,
                self.server.network,
                detected_at_str,
                last_seen_str,
                self._status_reason,
            )
        except Exception as exc:
            logger.debug("Slack offline notify failed: %s", exc)

    async def _notify_recovery(self, downtime_seconds: int) -> None:
        try:
            try:
                from ..slack_client import notify_recovery
            except ImportError:  # pragma: no cover - direct execution fallback
                from slack_client import notify_recovery
            recovered_at = self.last_seen.isoformat() if self.last_seen else None
            await notify_recovery(
                self.server.name,
                self.server.id,
                downtime_seconds,
                self.server.host,
                self.server.port,
                self.server.network,
                recovered_at,
            )
        except Exception as exc:
            logger.debug("Slack recovery notify failed: %s", exc)

    async def _notify_degraded(self) -> None:
        try:
            try:
                from ..slack_client import notify_degraded
            except ImportError:  # pragma: no cover - direct execution fallback
                from slack_client import notify_degraded
            detected_at = self.last_seen.isoformat() if self.last_seen else None
            await notify_degraded(
                self.server.name,
                self.server.id,
                self.server.host,
                self.server.port,
                self.server.network,
                detected_at,
                self._status_reason,
            )
        except Exception as exc:
            logger.debug("Slack degraded notify failed: %s", exc)

    async def _notify_connection_alert(self, elapsed_seconds: int) -> None:
        try:
            try:
                from ..slack_client import notify_connection_alert
            except ImportError:  # pragma: no cover - direct execution fallback
                from slack_client import notify_connection_alert
            detected_at_dt = self._failure_started_at or self._offline_since
            detected_at = detected_at_dt.isoformat() if detected_at_dt else None
            last_seen = self.last_seen.isoformat() if self.last_seen else None
            await notify_connection_alert(
                self.server.name,
                self.server.id,
                elapsed_seconds,
                self.server.host,
                self.server.port,
                self.server.network,
                detected_at,
                last_seen,
                self._status_reason,
            )
        except Exception as exc:
            logger.debug("Slack connection alert notify failed: %s", exc)
