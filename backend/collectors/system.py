"""Parse CPU/RAM metrics from remote server output."""
from dataclasses import dataclass


@dataclass
class SystemInfo:
    cpu_percent: float
    ram_used: int    # MB
    ram_total: int   # MB


# /proc 기반 — psutil 불필요, Linux 범용
SYSTEM_CMD_PROC = r"""python3 -c "
import re, time
with open('/proc/stat') as f: c1 = f.readline().split()
time.sleep(0.1)
with open('/proc/stat') as f: c2 = f.readline().split()
total1 = sum(int(x) for x in c1[1:])
idle1  = int(c1[4])
total2 = sum(int(x) for x in c2[1:])
idle2  = int(c2[4])
cpu = 100.0 * (1 - (idle2-idle1)/(total2-total1))
mem = {}
with open('/proc/meminfo') as f:
    for line in f:
        k, v = line.split(':')
        mem[k.strip()] = int(v.strip().split()[0])
ram_total = mem['MemTotal'] * 1024
ram_free  = (mem.get('MemAvailable') or mem['MemFree']) * 1024
ram_used  = ram_total - ram_free
print(f'{cpu:.1f},{ram_used},{ram_total}')
" """

# psutil 기반 fallback
SYSTEM_CMD_PSUTIL = (
    'python3 -c "'
    'import psutil; m=psutil.virtual_memory(); '
    'print(f\"{psutil.cpu_percent(interval=0.1):.1f},{m.used},{m.total}\")"'
)

# 하위 호환 — 기존 코드가 SYSTEM_CMD 를 직접 임포트하는 경우 대비
SYSTEM_CMD = SYSTEM_CMD_PROC


def parse_system(raw: str) -> SystemInfo:
    """csv 'cpu,ram_used_bytes,ram_total_bytes' 형식 파싱."""
    parts = raw.strip().split(",")
    cpu = float(parts[0])
    ram_used_bytes = int(parts[1])
    ram_total_bytes = int(parts[2])
    return SystemInfo(
        cpu_percent=cpu,
        ram_used=ram_used_bytes // 1024 // 1024,
        ram_total=ram_total_bytes // 1024 // 1024,
    )
