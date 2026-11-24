"""Parse and publish timestamped hardware observations for the System draft."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, delete, func

try:
    from .models import Server, HardwareRound, HardwarePowerObservation, HardwareImport, PowerRawSample
    from . import power_logging, temperature_history, server_history
except ImportError:
    from models import Server, HardwareRound, HardwarePowerObservation, HardwareImport, PowerRawSample
    import power_logging, temperature_history, server_history
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class HardwareSensor(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    name: str
    value: float | None
    unit: str
    kind: Literal['temperature', 'fan', 'power', 'voltage']
    status: str
    upper_warning: float | None = None
    upper_critical: float | None = None


class HardwareSnapshot(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    source: Literal['ssh-ipmi-snapshot', 'ssh-ipmi'] = 'ssh-ipmi-snapshot'
    collected_at: datetime
    bmc_ip: str | None = None
    power_watts: float | None = Field(default=None, ge=0)
    sensors: list[HardwareSensor] = Field(default_factory=list, max_length=512)


def _number(raw: str) -> float | None:
    try:
        value = float(raw)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def parse_sensor_snapshot(raw: str, collected_at: str, bmc_ip: str | None) -> dict:
    sensors = []
    kinds = {'degrees C': 'temperature', 'RPM': 'fan', 'Watts': 'power', 'Volts': 'voltage'}
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split('|')]
        if len(parts) < 4 or parts[2] not in kinds:
            continue
        sensors.append(HardwareSensor(
            name=parts[0], value=_number(parts[1]), unit=parts[2],
            kind=kinds[parts[2]], status=parts[3],
            upper_warning=_number(parts[7]) if len(parts) > 7 else None,
            upper_critical=_number(parts[8]) if len(parts) > 8 else None,
        ))
    power = re.search(r'Instantaneous power reading:\s*(\d+(?:\.\d+)?)\s+Watts', raw)
    snapshot = HardwareSnapshot(
        collected_at=collected_at, bmc_ip=bmc_ip,
        power_watts=float(power.group(1)) if power else None, sensors=sensors,
    )
    return snapshot.model_dump(mode='json')


def read_hardware_snapshots(path: Path) -> dict:
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            return {}
    except (OSError, ValueError):
        return {}
    result = {}
    for server_id, value in raw.items():
        if not str(server_id).isdigit():
            continue
        try:
            result[str(int(server_id))] = HardwareSnapshot.model_validate(value).model_dump(mode='json')
        except (ValidationError, TypeError, ValueError):
            continue
    return result


logger = logging.getLogger(__name__)
SENSOR_COMMAND = 'sudo -n /usr/bin/ipmitool sensor list'
POWER_COMMAND = 'sudo -n /usr/bin/ipmitool dcmi power reading'


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _naive(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _eligible(servers):
    return [server for server in servers if server.name.casefold() != 'poseidon']


def _point(at, values):
    observed = [value for value in values if value is not None]
    complete = bool(values) and len(observed) == len(values)
    point = {'collected_at': _utc(at).isoformat(), 'power_watts': sum(observed) if complete else None,
             'server_count': len(observed), 'expected_count': len(values), 'complete': complete}
    if observed and not complete:
        point['observed_watts'] = sum(observed)
    return point


class HardwareMonitor:
    """Independent, bounded hardware worker; never shares GPU SSH connections."""
    def __init__(self, sessions, *, interval=60, concurrency=3, ssh_factory=None, gpu_archive_interval=60):
        self.sessions = sessions
        self.interval = max(30, interval)
        self.gpu_archive_interval = max(1, gpu_archive_interval)
        self.ssh_factory = ssh_factory
        self.executor = ThreadPoolExecutor(max_workers=max(1, min(concurrency, 7)), thread_name_prefix='hardware-ipmi')
        self.current = None

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _collect(self, server):
        factory = self.ssh_factory
        if factory is None:
            try:
                from .collectors.ssh_client import SSHClient
            except ImportError:
                from collectors.ssh_client import SSHClient
            factory = SSHClient
        ssh = factory(server)
        outputs = []
        try:
            ssh.connect()
            for command in (SENSOR_COMMAND, POWER_COMMAND):
                try:
                    outputs.append(ssh.run_bounded(command, timeout=15))
                except Exception:
                    # No command output/credentials in logs; one unsupported reading
                    # must not discard independent sensors from the other command.
                    logger.debug('Hardware command unavailable for server %s', server.id)
        except Exception:
            logger.debug('Hardware connection unavailable for server %s', server.id)
        finally:
            ssh.close()
        snapshot = parse_sensor_snapshot('\n'.join(outputs), datetime.now(timezone.utc).isoformat(), None)
        snapshot['source'] = 'ssh-ipmi'
        return snapshot

    async def collect_round(self):
        async with self.sessions() as db:
            servers = _eligible((await db.execute(select(Server).order_by(Server.id))).scalars().all())
        loop = asyncio.get_running_loop()
        targets = [server for server in servers if server.network == 'internal']
        results = await asyncio.gather(*(loop.run_in_executor(self.executor, self._collect, server) for server in targets), return_exceptions=True)
        snapshots = {}
        for server, result in zip(targets, results):
            if isinstance(result, BaseException):
                logger.warning('Hardware observation unavailable for server %s', server.id)
                result = parse_sensor_snapshot('', datetime.now(timezone.utc).isoformat(), None)
                result['source'] = 'ssh-ipmi'
            snapshots[str(server.id)] = result
        at = datetime.now(timezone.utc)
        await self.archive_round(servers, snapshots, at)
        # Use the identical latest actual host observation as the persisted
        # internal-network series, so WS/API merges identify the same round.
        point_at = max((power_logging.parse_at(snapshot['collected_at']) for snapshot in snapshots.values()), default=at)
        point = _point(point_at, [snapshots.get(str(server.id), {}).get('power_watts') for server in servers if server.network == 'internal'])
        self.current = {'mode': 'live', 'servers': snapshots, 'point': point}
        return self.current

    async def archive_round(self, servers, snapshots, at, *, db=None):
        async def write(session):
            round_ = HardwareRound(collected_at=_naive(at))
            session.add(round_)
            await session.flush()
            for server in _eligible(servers):
                snapshot = snapshots.get(str(server.id), {})
                observed_at = snapshot.get('collected_at')
                session.add(HardwarePowerObservation(round_id=round_.id, server_id=server.id,
                    network=server.network, power_watts=snapshot.get('power_watts'),
                    observed_at=_naive(datetime.fromisoformat(observed_at.replace('Z', '+00:00'))) if observed_at else None))
            await session.flush()
            await power_logging.record_round(session, round_.id, _eligible(servers), snapshots, at, self.interval)
            await temperature_history.record_round(session, round_.id, _eligible(servers), snapshots, at, self.interval)
        if db is not None:
            await write(db)
        else:
            async with self.sessions() as session:
                await write(session)
                await session.commit()

    async def seed_snapshots(self, servers, snapshots):
        """Import genuine observations once, grouping the known one-minute audit batch."""
        async with self.sessions() as db:
            if await db.get(HardwareImport, 'hardware-snapshots-v1'):
                return
            groups = {}
            allowed = {str(server.id) for server in _eligible(servers)}
            for server_id, snapshot in snapshots.items():
                if server_id in allowed:
                    groups.setdefault(snapshot['collected_at'], {})[server_id] = snapshot
            times = [datetime.fromisoformat(at.replace('Z', '+00:00')) for at in groups]
            if times and (max(times) - min(times)).total_seconds() <= 60:
                # These are the prior audit batch. Preserve the actual per-server
                # observation times and use its measured end time for the round.
                groups = {max(times).isoformat(): {key: value for group in groups.values() for key, value in group.items()}}
            for at, observations in sorted(groups.items()):
                await self.archive_round(servers, observations, datetime.fromisoformat(at.replace('Z', '+00:00')), db=db)
            db.add(HardwareImport(key='hardware-snapshots-v1'))
            await db.commit()

    async def initialize_power_logging(self, snapshots=None):
        """Idempotently import every actual legacy observation, without invented PSU history."""
        snapshots = snapshots or {}
        while True:
            async with self.sessions() as db:
                rounds = (await db.execute(select(HardwareRound).where(~HardwareRound.id.in_(select(PowerRawSample.round_id)), HardwareRound.id.in_(select(HardwarePowerObservation.round_id))).order_by(HardwareRound.collected_at).limit(100))).scalars().all()
                if not rounds:
                    return
                for round_ in rounds:
                    observations = (await db.execute(select(HardwarePowerObservation).where(HardwarePowerObservation.round_id == round_.id))).scalars().all()
                    if not observations:
                        continue
                    from types import SimpleNamespace
                    servers = [SimpleNamespace(id=row.server_id,network=row.network) for row in observations]
                    observed = {}
                    for row in observations:
                        at = _utc(row.observed_at or round_.collected_at)
                        original = snapshots.get(str(row.server_id), {})
                        sensors = original.get('sensors', []) if original.get('collected_at') and power_logging.parse_at(original['collected_at']) == at else []
                        observed[str(row.server_id)] = {'power_watts':row.power_watts,'collected_at':at.isoformat(),'sensors':sensors}
                    await power_logging.record_round(db, round_.id, servers, observed, round_.collected_at, self.interval)
                await db.commit()

    async def cleanup(self, *, now=None):
        now = now or datetime.now(timezone.utc)
        async with self.sessions() as db:
            cutoff = await power_logging.prune(db, now)
            await temperature_history.prune(db, cutoff)
            old_ids = select(HardwareRound.id).where(HardwareRound.collected_at < cutoff, ~HardwareRound.id.in_(select(PowerRawSample.round_id)))
            await db.execute(delete(HardwarePowerObservation).where(HardwarePowerObservation.round_id.in_(old_ids)))
            await db.execute(delete(HardwareRound).where(HardwareRound.collected_at < cutoff, ~HardwareRound.id.in_(select(PowerRawSample.round_id))))
            await db.commit()

    async def power_history(self, hours, network, *, now=None, source='dcmi'):
        async with self.sessions() as db:
            result = await power_logging.history(db, min(168,max(1,hours)), network, source, now or datetime.now(timezone.utc), self.interval)
        result['points'] = power_logging.downsample_history(result['points'])
        return result

    async def power_rollups(self, resolution, days, network, source='dcmi', *, now=None, after=None):
        async with self.sessions() as db:
            return await power_logging.read_rollups(db,resolution,days,network,source,now or datetime.now(timezone.utc),after=after)

    async def server_history(self,hours,network,*,now=None):
        async with self.sessions() as db:
            return await server_history.history(db,min(168,max(1,hours)),network,now or datetime.now(timezone.utc),self.gpu_archive_interval)

    async def temperature_history(self, hours, network, *, now=None):
        async with self.sessions() as db:
            return await temperature_history.history(db,min(168,max(1,hours)),network,now or datetime.now(timezone.utc),self.interval,self.gpu_archive_interval)

    async def run(self, broadcast):
        try:
            while True:
                started = asyncio.get_running_loop().time()
                try:
                    payload = await self.collect_round()
                    await broadcast({'type': 'hardware_update', 'data': payload})
                    await self.cleanup()
                except Exception:
                    logger.warning('Hardware collection round failed')
                elapsed = asyncio.get_running_loop().time() - started
                await asyncio.sleep(max(1, self.interval - elapsed))
        finally:
            self.close()
