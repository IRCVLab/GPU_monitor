"""Actual CPU sensor history and the existing GPU archive, with no invented past."""
import math
import re
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,delete,func,case
from sqlalchemy.dialects.sqlite import insert
try:
    from .models import CpuTemperatureObservation,GpuMetric,Server
    from .power_logging import utc,naive,parse_at
except ImportError:
    from models import CpuTemperatureObservation,GpuMetric,Server
    from power_logging import utc,naive,parse_at


def cpu_sensor(name):
    normalized=re.sub(r'[_-]+',' ',name).strip()
    return bool(re.fullmatch(r'(?:CPU\s*\d*\s*(?:Package\s*)?Temp(?:erature)?|Temp(?:erature)?\s*CPU\s*\d*)',normalized,re.I))


def temperature(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and 0<=value<=150 else None
    except (TypeError,ValueError):return None


async def record_round(db,round_id,servers,snapshots,at,interval):
    rows=[]
    for server in servers:
        if server.name.casefold()=='poseidon':continue
        snapshot=snapshots.get(str(server.id),{})
        observed=parse_at(snapshot['collected_at']) if snapshot.get('collected_at') else utc(at)
        sensors={sensor['name']:sensor for sensor in snapshot.get('sensors',[]) if sensor.get('kind')=='temperature' and cpu_sensor(sensor.get('name',''))}
        if not sensors:sensors={'__unavailable__':{'value':None}}
        for name,sensor in sensors.items():
            value=temperature(sensor.get('value'))
            if str(sensor.get('status','')).lower() in ('na','n/a','ns','unavailable','disabled','not present'):value=None
            rows.append(dict(round_id=round_id,server_id=server.id,network=server.network,sensor_name=name,temperature=value,observed_at=naive(observed),interval_seconds=interval))
    for offset in range(0,len(rows),50):
        await db.execute(insert(CpuTemperatureObservation).values(rows[offset:offset+50]).on_conflict_do_nothing(index_elements=['round_id','server_id','sensor_name']))


async def prune(db,cutoff):
    await db.execute(delete(CpuTemperatureObservation).where(CpuTemperatureObservation.observed_at<naive(cutoff)))


def point(at,maximum,average,count,interval):
    return dict(collected_at=utc(at).isoformat(),max_celsius=maximum,avg_celsius=average,device_count=count,_interval=interval)


def with_gaps(points,now):
    output=[]
    for row in points:
        if output:
            previous=output[-1]
            a=parse_at(previous['collected_at']);b=parse_at(row['collected_at'])
            threshold=2.5*min(previous['_interval'],row['_interval'])
            if (b-a).total_seconds()>threshold:
                output.append(point(a+timedelta(seconds=previous['_interval']),None,None,0,previous['_interval']))
        output.append(row)
    if output:
        last=output[-1]
        at=parse_at(last['collected_at'])
        if last['max_celsius'] is not None and (utc(now)-at).total_seconds()>2.5*last['_interval']:
            output.append(point(at+timedelta(seconds=last['_interval']),None,None,0,last['_interval']))
    return output


def downsample(points,limit=1500):
    """Keep observed peaks and explicit missing markers, with bounded output.

    Coarse averages weight each actual device observation; one bucket contributes
    its actual peak timestamp, plus first/last missing markers if present.
    """
    if len(points)<=limit:return [{key:value for key,value in row.items() if not key.startswith('_')} for row in points]
    first=parse_at(points[0]['collected_at']).timestamp();last=parse_at(points[-1]['collected_at']).timestamp()
    width=max(1,(last-first)/(limit//3-1))
    buckets=defaultdict(list)
    for row in points:buckets[int((parse_at(row['collected_at']).timestamp()-first)//width)].append(row)
    output=[]
    for rows in buckets.values():
        valid=[row for row in rows if row['max_celsius'] is not None]
        missing=[row for row in rows if row['max_celsius'] is None]
        selected=[]
        if valid:
            peak=dict(max(valid,key=lambda row:row['max_celsius']))
            count=sum(row['device_count'] for row in valid)
            peak['avg_celsius']=sum(row['avg_celsius']*row['device_count'] for row in valid)/count if count else None
            # Keep the physical device count at the actual peak observation;
            # distinguish the observations used for this coarse average.
            peak['observation_count']=count
            selected.append(peak)
        if missing:
            selected.append(missing[0])
            if missing[-1] is not missing[0]:selected.append(missing[-1])
        output.extend(sorted(selected,key=lambda row:row['collected_at']))
    return [{key:value for key,value in row.items() if not key.startswith('_')} for row in output]


async def history(db,hours,network,now,cpu_interval,gpu_interval=60):
    end=utc(now);start=end-timedelta(hours=hours)
    cpu=CpuTemperatureObservation
    query=select(func.max(cpu.observed_at),func.max(cpu.temperature),func.avg(cpu.temperature),func.count(cpu.temperature),func.min(cpu.interval_seconds)).where(cpu.observed_at>=naive(start),cpu.observed_at<=naive(end))
    if network!='all':query=query.where(cpu.network==network)
    query=query.group_by(cpu.round_id).order_by(func.max(cpu.observed_at))
    cpu_rows=(await db.execute(query)).all()
    cpu_points=[point(at,maximum,average,count,interval) for at,maximum,average,count,interval in cpu_rows]

    # The older parser used 0 for unavailable GPU temperatures. Exclude that
    # historical sentinel along with invalid values; do not invent missing data.
    gpu=GpuMetric
    valid=case((gpu.temperature.between(1,150),gpu.temperature),else_=None)
    minute=func.strftime('%Y-%m-%dT%H:%M:00',gpu.collected_at)
    device_query=select(minute.label('minute'),func.max(gpu.collected_at).label('at'),func.max(valid).label('maximum'),func.avg(valid).label('average')).join(Server,Server.id==gpu.server_id).where(gpu.collected_at>=naive(start),gpu.collected_at<=naive(end),func.lower(Server.name)!='poseidon')
    if network!='all':device_query=device_query.where(Server.network==network)
    devices=device_query.group_by(minute,gpu.server_id,gpu.gpu_index).subquery()
    gpu_query=select(func.max(devices.c.at),func.max(devices.c.maximum),func.avg(devices.c.average),func.count(devices.c.average)).group_by(devices.c.minute).order_by(func.max(devices.c.at))
    gpu_rows=(await db.execute(gpu_query)).all()
    gpu_points=[point(at,maximum,average,count,max(1,gpu_interval)) for at,maximum,average,count in gpu_rows]
    return {'cpu':downsample(with_gaps(cpu_points,end)),'gpu':downsample(with_gaps(gpu_points,end))}
