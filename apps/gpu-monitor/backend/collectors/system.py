"""Parse CPU/RAM metrics from remote server output."""
from dataclasses import dataclass
import math


@dataclass
class SystemInfo:
    cpu_percent: float
    ram_used: int    # MB
    ram_total: int   # MB
    io_pressure_some: float | None = None
    io_pressure_full: float | None = None
    io_blocked_tasks: int | None = None
    io_pressure_supported: bool = False
    disk_read_bytes_total: int | None = None
    disk_write_bytes_total: int | None = None
    disk_sample_time: float | None = None
    pci_gpu_count: int | None = None
    cpu_pressure_some: float | None = None
    cpu_running_tasks: int | None = None
    load_avg_1: float | None = None
    load_avg_5: float | None = None
    load_avg_15: float | None = None
    cpu_count: int | None = None
    network_rx_bytes_total: int | None = None
    network_tx_bytes_total: int | None = None
    network_sample_time: float | None = None
    network_interface_counters: dict[str, tuple[int,int]] | None = None
    network_rx_bytes_per_second: float | None = None
    network_tx_bytes_per_second: float | None = None


@dataclass
class DiskIORate:
    read_bytes_per_second: float
    write_bytes_per_second: float


# /proc 기반 — psutil 불필요, Linux 범용
SYSTEM_CMD_PROC = (
    'python3 -c "\n'
    'import glob\n'
    'import os\n'
    'import time\n'
    '\n'
    'def read_cpu_and_task_counts():\n'
    '    cpu_parts = None\n'
    "    blocked = ''\n"
    "    running = ''\n"
    "    with open('/proc/stat') as f:\n"
    '        for index, line in enumerate(f):\n'
    '            if index == 0:\n'
    '                cpu_parts = line.split()\n'
    "            elif line.startswith('procs_blocked '):\n"
    '                blocked = line.split()[1]\n'
    "            elif line.startswith('procs_running '):\n"
    '                running = line.split()[1]\n'
    '            if blocked and running:\n'
    '                break\n'
    '    return cpu_parts, blocked, running\n'
    '\n'
    'def read_psi_some_avg10(path):\n'
    '    try:\n'
    "        with open(path) as f:\n"
    '            for line in f:\n'
    '                tokens = line.split()\n'
    "                if not tokens or tokens[0] != 'some':\n"
    '                    continue\n'
    '                for token in tokens[1:]:\n'
    "                    if token.startswith('avg10='):\n"
    "                        value = token.split('=', 1)[1]\n"
    '                        float(value)\n'
    '                        return value\n'
    '    except Exception:\n'
    '        pass\n'
    "    return ''\n"
    '\n'
    'def read_io_psi_avg10():\n'
    '    try:\n'
    '        values = {}\n'
    "        with open('/proc/pressure/io') as f:\n"
    '            for line in f:\n'
    '                tokens = line.split()\n'
    '                if not tokens:\n'
    '                    continue\n'
    '                label = tokens[0]\n'
    '                for token in tokens[1:]:\n'
    "                    if token.startswith('avg10='):\n"
    "                        value = token.split('=', 1)[1]\n"
    '                        float(value)\n'
    '                        values[label] = value\n'
    "        return values.get('some', ''), values.get('full', '')\n"
    '    except Exception:\n'
    "        return '', ''\n"
    '\n'
    'def read_disk_counters():\n'
    '    try:\n'
    '        physical = set()\n'
    "        for path in glob.glob('/sys/block/*'):\n"
    "            if os.path.islink(os.path.join(path, 'device')):\n"
    '                physical.add(os.path.basename(path))\n'
    '        read_sectors = 0\n'
    '        write_sectors = 0\n'
    "        with open('/proc/diskstats') as f:\n"
    '            for line in f:\n'
    '                fields = line.split()\n'
    '                if len(fields) < 10 or fields[2] not in physical:\n'
    '                    continue\n'
    '                read_sectors += int(fields[5])\n'
    '                write_sectors += int(fields[9])\n'
    '        return read_sectors * 512, write_sectors * 512, time.monotonic()\n'
    '    except Exception:\n'
    "        return '', '', ''\n"
    '\n'
    'def count_nvidia_display_pci_devices():\n'
    '    count = 0\n'
    "    for path in glob.glob('/sys/bus/pci/devices/*'):\n"
    '        try:\n'
    "            with open(os.path.join(path, 'vendor')) as f:\n"
    '                vendor = f.read().strip().lower()\n'
    "            with open(os.path.join(path, 'class')) as f:\n"
    '                pci_class = f.read().strip().lower()\n'
    "            if vendor == '0x10de' and pci_class.startswith('0x03'):\n"
    '                count += 1\n'
    '        except Exception:\n'
    '            continue\n'
    '    return count\n'
    '\n'
    'def read_loadavg():\n'
    '    try:\n'
    '        return os.getloadavg()\n'
    '    except Exception:\n'
    "        return '', '', ''\n"
    '\n'
    'def read_cpu_count():\n'
    '    count = os.cpu_count()\n'
    "    return '' if count is None else count\n"
    '\n'
    'c1, _, _ = read_cpu_and_task_counts()\n'
    'time.sleep(0.1)\n'
    'c2, blocked, running = read_cpu_and_task_counts()\n'
    'total1 = sum(int(x) for x in c1[1:])\n'
    'idle1  = int(c1[4])\n'
    'total2 = sum(int(x) for x in c2[1:])\n'
    'idle2  = int(c2[4])\n'
    'cpu = 100.0 * (1 - (idle2-idle1)/(total2-total1))\n'
    'mem = {}\n'
    "with open('/proc/meminfo') as f:\n"
    '    for line in f:\n'
    "        k, v = line.split(':')\n"
    '        mem[k.strip()] = int(v.strip().split()[0])\n'
    "ram_total = mem['MemTotal'] * 1024\n"
    "ram_free  = (mem.get('MemAvailable') or mem['MemFree']) * 1024\n"
    'ram_used  = ram_total - ram_free\n'
    "cpu_some = read_psi_some_avg10('/proc/pressure/cpu')\n"
    'psi_some, psi_full = read_io_psi_avg10()\n'
    'disk_read, disk_write, disk_sample_time = read_disk_counters()\n'
    'pci_gpu_count = count_nvidia_display_pci_devices()\n'
    'load_avg_1, load_avg_5, load_avg_15 = read_loadavg()\n'
    'cpu_count = read_cpu_count()\n'
    "print(f'{cpu:.1f},{ram_used},{ram_total},{psi_some},{psi_full},{blocked},{disk_read},{disk_write},{disk_sample_time},{pci_gpu_count},{cpu_some},{running},{load_avg_1},{load_avg_5},{load_avg_15},{cpu_count}')\n"
    '"'
)

# psutil 기반 fallback
SYSTEM_CMD_PSUTIL = (
    'python3 -c "'
    'import psutil; m=psutil.virtual_memory(); '
    'print(f\"{psutil.cpu_percent(interval=0.1):.1f},{m.used},{m.total}\")"'
)

NETWORK_READER_SOURCE = """
import glob
import os
import time

def read_network_counters():
    try:
        physical = {os.path.basename(path) for path in glob.glob('/sys/class/net/*')
                    if os.path.islink(os.path.join(path, 'device'))}
        if not physical:
            return '', '', '', ''
        counters = {}
        with open('/proc/net/dev') as handle:
            for line in handle:
                if ':' not in line:
                    continue
                name, fields = line.rsplit(':', 1)
                name = name.strip()
                if name not in physical:
                    continue
                values = fields.split()
                if len(values) < 16:
                    return '', '', '', ''
                rx, tx = int(values[0]), int(values[8])
                if rx < 0 or tx < 0:
                    return '', '', '', ''
                counters[name] = (rx, tx)
        if set(counters) != physical:
            return '', '', '', ''
        identities = ';'.join(name + ':' + str(counters[name][0]) + ':' + str(counters[name][1]) for name in sorted(counters))
        return sum(value[0] for value in counters.values()), sum(value[1] for value in counters.values()), time.monotonic(), identities
    except Exception:
        return '', '', '', ''
"""
SYSTEM_CMD_PROC = SYSTEM_CMD_PROC.replace('def count_nvidia_display_pci_devices():', NETWORK_READER_SOURCE + '\n\ndef count_nvidia_display_pci_devices():')
SYSTEM_CMD_PROC = SYSTEM_CMD_PROC.replace('pci_gpu_count = count_nvidia_display_pci_devices()', 'network_rx, network_tx, network_time, network_interfaces = read_network_counters()\npci_gpu_count = count_nvidia_display_pci_devices()')
SYSTEM_CMD_PROC = SYSTEM_CMD_PROC.replace("{cpu_count}')", "{cpu_count},{network_rx},{network_tx},{network_time},{network_interfaces}')")

# 하위 호환 — 기존 코드가 SYSTEM_CMD 를 직접 임포트하는 경우 대비
SYSTEM_CMD = SYSTEM_CMD_PROC


def _parse_optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_optional_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_system(raw: str) -> SystemInfo:
    """Parse csv 'cpu,ram_used_bytes,ram_total_bytes[,io_some,io_full,procs_blocked[,disk_read,disk_write,disk_time,pci_gpus[,cpu_some,procs_running[,load1,load5,load15,cpu_count]]]]' output."""
    parts = raw.strip().split(',')
    if len(parts) < 3:
        raise ValueError(f'Invalid system metrics payload: {raw!r}')

    cpu = float(parts[0])
    ram_used_bytes = int(parts[1])
    ram_total_bytes = int(parts[2])

    io_pressure_some = None
    io_pressure_full = None
    io_blocked_tasks = None
    io_pressure_supported = False
    disk_read_bytes_total = None
    disk_write_bytes_total = None
    disk_sample_time = None
    pci_gpu_count = None
    cpu_pressure_some = None
    cpu_running_tasks = None
    load_avg_1 = None
    load_avg_5 = None
    load_avg_15 = None
    cpu_count = None

    if len(parts) >= 6:
        parsed_some = _parse_optional_float(parts[3])
        parsed_full = _parse_optional_float(parts[4])
        if parsed_some is not None and parsed_full is not None:
            io_pressure_some = parsed_some
            io_pressure_full = parsed_full
            io_pressure_supported = True
        io_blocked_tasks = _parse_optional_int(parts[5])

    if len(parts) >= 10:
        disk_read_bytes_total = _parse_optional_int(parts[6])
        disk_write_bytes_total = _parse_optional_int(parts[7])
        disk_sample_time = _parse_optional_float(parts[8])
        pci_gpu_count = _parse_optional_int(parts[9])

    if len(parts) >= 12:
        cpu_pressure_some = _parse_optional_float(parts[10])
        cpu_running_tasks = _parse_optional_int(parts[11])

    if len(parts) >= 16:
        load_avg_1 = _parse_optional_float(parts[12])
        load_avg_5 = _parse_optional_float(parts[13])
        load_avg_15 = _parse_optional_float(parts[14])
        cpu_count = _parse_optional_int(parts[15])

    return SystemInfo(
        cpu_percent=cpu,
        ram_used=ram_used_bytes // 1024 // 1024,
        ram_total=ram_total_bytes // 1024 // 1024,
        io_pressure_some=io_pressure_some,
        io_pressure_full=io_pressure_full,
        io_blocked_tasks=io_blocked_tasks,
        io_pressure_supported=io_pressure_supported,
        disk_read_bytes_total=disk_read_bytes_total,
        disk_write_bytes_total=disk_write_bytes_total,
        disk_sample_time=disk_sample_time,
        pci_gpu_count=pci_gpu_count,
        cpu_pressure_some=cpu_pressure_some,
        cpu_running_tasks=cpu_running_tasks,
        load_avg_1=load_avg_1,
        load_avg_5=load_avg_5,
        load_avg_15=load_avg_15,
        cpu_count=cpu_count,
        **_parse_network_fields(parts),
    )


def calculate_disk_io_rate(previous: SystemInfo | None, current: SystemInfo | None) -> DiskIORate | None:
    """Calculate non-negative disk I/O rates between two cumulative samples."""
    if previous is None or current is None:
        return None

    required = (
        previous.disk_read_bytes_total,
        previous.disk_write_bytes_total,
        previous.disk_sample_time,
        current.disk_read_bytes_total,
        current.disk_write_bytes_total,
        current.disk_sample_time,
    )
    if any(value is None for value in required):
        return None

    elapsed = current.disk_sample_time - previous.disk_sample_time
    if elapsed <= 0:
        return None

    read_delta = current.disk_read_bytes_total - previous.disk_read_bytes_total
    write_delta = current.disk_write_bytes_total - previous.disk_write_bytes_total
    if read_delta < 0 or write_delta < 0:
        return None

    return DiskIORate(
        read_bytes_per_second=read_delta / elapsed,
        write_bytes_per_second=write_delta / elapsed,
    )


@dataclass
class NetworkIORate:
    rx_bytes_per_second: float
    tx_bytes_per_second: float


def _parse_network_fields(parts):
    if len(parts) < 20:
        return {}
    try:
        rx, tx, at = int(parts[16]), int(parts[17]), float(parts[18])
        if rx < 0 or tx < 0 or not math.isfinite(at) or at <= 0:
            return {}
        counters = {}
        for entry in parts[19].split(';'):
            name, received, sent = entry.rsplit(':', 2)
            values = (int(received), int(sent))
            if not name or name in counters or min(values) < 0:
                return {}
            counters[name] = values
        if not counters or sum(value[0] for value in counters.values()) != rx or sum(value[1] for value in counters.values()) != tx:
            return {}
        return dict(network_rx_bytes_total=rx, network_tx_bytes_total=tx, network_sample_time=at, network_interface_counters=counters)
    except (ValueError, TypeError):
        return {}


def calculate_network_io_rate(previous: SystemInfo | None, current: SystemInfo | None) -> NetworkIORate | None:
    if previous is None or current is None:
        return None
    before, after = previous.network_interface_counters, current.network_interface_counters
    if not before or not after or set(before) != set(after):
        return None
    if previous.network_sample_time is None or current.network_sample_time is None:
        return None
    elapsed = current.network_sample_time - previous.network_sample_time
    if not math.isfinite(elapsed) or elapsed <= 0:
        return None
    rx = tx = 0
    for name in before:
        delta_rx = after[name][0] - before[name][0]
        delta_tx = after[name][1] - before[name][1]
        if delta_rx < 0 or delta_tx < 0:
            return None
        rx += delta_rx; tx += delta_tx
    return NetworkIORate(rx / elapsed, tx / elapsed)
