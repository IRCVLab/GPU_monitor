"""Parse CPU/RAM metrics from remote server output."""
from dataclasses import dataclass


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
    'def read_cpu_and_blocked():\n'
    '    cpu_parts = None\n'
    "    blocked = ''\n"
    "    with open('/proc/stat') as f:\n"
    '        for index, line in enumerate(f):\n'
    '            if index == 0:\n'
    '                cpu_parts = line.split()\n'
    "            elif line.startswith('procs_blocked '):\n"
    '                blocked = line.split()[1]\n'
    '                break\n'
    '    return cpu_parts, blocked\n'
    '\n'
    'def read_psi_avg10():\n'
    '    try:\n'
    '        values = {}\n'
    "        with open('/proc/pressure/io') as f:\n"
    '            for line in f:\n'
    '                tokens = line.split()\n'
    '                if not tokens:\n'
    '                    continue\n'
    '                label = tokens[0]\n'
    '                metrics = {}\n'
    '                for token in tokens[1:]:\n'
    "                    if '=' in token:\n"
    "                        key, value = token.split('=', 1)\n"
    "                        metrics[key] = value\n"
    "                values[label] = metrics.get('avg10', '')\n"
    "        some = values.get('some', '')\n"
    "        full = values.get('full', '')\n"
    '        float(some)\n'
    '        float(full)\n'
    '        return some, full\n'
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
    'c1, _ = read_cpu_and_blocked()\n'
    'time.sleep(0.1)\n'
    'c2, blocked = read_cpu_and_blocked()\n'
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
    'psi_some, psi_full = read_psi_avg10()\n'
    'disk_read, disk_write, disk_sample_time = read_disk_counters()\n'
    'pci_gpu_count = count_nvidia_display_pci_devices()\n'
    "print(f'{cpu:.1f},{ram_used},{ram_total},{psi_some},{psi_full},{blocked},{disk_read},{disk_write},{disk_sample_time},{pci_gpu_count}')\n"
    '"'
)

# psutil 기반 fallback
SYSTEM_CMD_PSUTIL = (
    'python3 -c "'
    'import psutil; m=psutil.virtual_memory(); '
    'print(f\"{psutil.cpu_percent(interval=0.1):.1f},{m.used},{m.total}\")"'
)

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
    """Parse csv 'cpu,ram_used_bytes,ram_total_bytes[,io_some,io_full,procs_blocked[,disk_read,disk_write,disk_time,pci_gpus]]' output."""
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
