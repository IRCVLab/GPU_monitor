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


# /proc 기반 — psutil 불필요, Linux 범용
SYSTEM_CMD_PROC = (
    'python3 -c "\n'
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
    "print(f'{cpu:.1f},{ram_used},{ram_total},{psi_some},{psi_full},{blocked}')\n"
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
    """Parse csv 'cpu,ram_used_bytes,ram_total_bytes[,io_some,io_full,procs_blocked]' output."""
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

    if len(parts) >= 6:
        parsed_some = _parse_optional_float(parts[3])
        parsed_full = _parse_optional_float(parts[4])
        if parsed_some is not None and parsed_full is not None:
            io_pressure_some = parsed_some
            io_pressure_full = parsed_full
            io_pressure_supported = True
        io_blocked_tasks = _parse_optional_int(parts[5])

    return SystemInfo(
        cpu_percent=cpu,
        ram_used=ram_used_bytes // 1024 // 1024,
        ram_total=ram_total_bytes // 1024 // 1024,
        io_pressure_some=io_pressure_some,
        io_pressure_full=io_pressure_full,
        io_blocked_tasks=io_blocked_tasks,
        io_pressure_supported=io_pressure_supported,
    )
