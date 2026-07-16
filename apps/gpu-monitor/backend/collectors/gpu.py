"""Parse GPU metrics from gpustat --json SSH output."""
import json
from dataclasses import dataclass, field
from datetime import datetime

NVIDIA_SMI_CMD = (
    "bash -c 'nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,"
    "memory.total,temperature.gpu,power.draw,gpu_uuid "
    "--format=csv,noheader,nounits 2>/dev/null; "
    'echo "##PROCS##"; '
    "nvidia-smi --query-compute-apps=gpu_uuid,pid "
    "--format=csv,noheader,nounits 2>/dev/null; "
    'echo "##PS##"; '
    "ps -eo pid=,user= 2>/dev/null'"
)


@dataclass
class GpuInfo:
    index: int
    name: str
    utilization: int       # %
    memory_used: int       # MB
    memory_total: int      # MB
    temperature: int       # °C
    power_draw: int        # W (rounded)
    users: list[str] = field(default_factory=list)  # unique usernames


@dataclass
class ServerGpuData:
    gpus: list[GpuInfo]
    collected_at: datetime


def parse_gpustat(json_str: str) -> ServerGpuData:
    """Parse gpustat --json output into ServerGpuData."""
    data = json.loads(json_str)
    gpus: list[GpuInfo] = []

    for gpu in data.get("gpus", []):
        processes = gpu.get("processes") or []
        users = sorted({p["username"] for p in processes if p.get("username")})

        gpus.append(GpuInfo(
            index=int(gpu["index"]),
            name=str(gpu.get("name", "")),
            utilization=int(gpu.get("utilization.gpu", 0) or 0),
            memory_used=int(gpu.get("memory.used", 0) or 0),
            memory_total=int(gpu.get("memory.total", 0) or 0),
            temperature=int(gpu.get("temperature.gpu", 0) or 0),
            power_draw=round(gpu.get("power.draw", 0) or 0),
            users=users,
        ))

    return ServerGpuData(gpus=gpus, collected_at=datetime.utcnow())


def parse_nvidia_smi(output: str) -> ServerGpuData:
    """Parse combined nvidia-smi output (NVIDIA_SMI_CMD) into ServerGpuData."""
    # Split into three sections
    parts = output.split("##PROCS##", 1)
    gpu_section = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    proc_parts = rest.split("##PS##", 1)
    proc_section = proc_parts[0]
    ps_section = proc_parts[1] if len(proc_parts) > 1 else ""

    # Section 3: pid → username
    pid_to_user: dict[str, str] = {}
    for line in ps_section.splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split(None, 1)
        if len(tokens) == 2:
            pid_to_user[tokens[0].strip()] = tokens[1].strip()

    # Section 2: gpu_uuid → set of usernames (via pid lookup)
    uuid_to_users: dict[str, set[str]] = {}
    for line in proc_section.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 2:
            continue
        gpu_uuid, pid = cols[0], cols[1]
        user = pid_to_user.get(pid)
        if user:
            uuid_to_users.setdefault(gpu_uuid, set()).add(user)

    # Section 1: GPU stats
    gpus: list[GpuInfo] = []
    for line in gpu_section.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 8:
            continue
        index, name, utilization, memory_used, memory_total, temperature, power_draw, gpu_uuid = (
            cols[0], cols[1], cols[2], cols[3], cols[4], cols[5], cols[6], cols[7]
        )
        try:
            power_int = round(float(power_draw)) if power_draw not in ("", "[N/A]", "N/A") else 0
        except ValueError:
            power_int = 0
        users = sorted(uuid_to_users.get(gpu_uuid, set()))
        gpus.append(GpuInfo(
            index=int(index),
            name=name,
            utilization=int(utilization) if utilization.lstrip("-").isdigit() else 0,
            memory_used=int(memory_used) if memory_used.lstrip("-").isdigit() else 0,
            memory_total=int(memory_total) if memory_total.lstrip("-").isdigit() else 0,
            temperature=int(temperature) if temperature.lstrip("-").isdigit() else 0,
            power_draw=power_int,
            users=users,
        ))

    return ServerGpuData(gpus=gpus, collected_at=datetime.utcnow())
