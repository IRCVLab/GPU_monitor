"""Bounded server-card histories from measured archives; no synthetic values."""
import math
import re
from datetime import datetime,timedelta
from sqlalchemy import select,func,case,and_,cast,String,literal
try:
    from .models import Server,PowerRawSample,PowerPsuReading,CpuTemperatureObservation,GpuMetric,NetworkMetric
    from .telemetry_time import utc,utc_naive
except ImportError:
    from models import Server,PowerRawSample,PowerPsuReading,CpuTemperatureObservation,GpuMetric,NetworkMetric
    from telemetry_time import utc,utc_naive


class Series:
    """Stream at most three entries per coarse bucket: peak and missing edges."""
    def __init__(self,start,end,limit=120):
        self.start=utc(start);self.end=utc(end);self.limit=limit
        self.width=max(1,(self.end-self.start).total_seconds()/(limit//3-1))
        self.small=[];self.buckets={};self.previous=None;self.seen_value=False

    def _append(self,at,value):
        point={'collected_at':utc(at).isoformat(),'value':value}
        if self.small is not None:
            self.small.append(point)
            if len(self.small)<=self.limit:return
            old=self.small;self.small=None
            for item in old:self._bucket(item)
        else:self._bucket(point)

    def _bucket(self,point):
        at=datetime.fromisoformat(point['collected_at'])
        key=int((at-self.start).total_seconds()//self.width)
        bucket=self.buckets.setdefault(key,{'peak':None,'first':None,'last':None})
        if point['value'] is None:
            bucket['first']=bucket['first'] or point;bucket['last']=point
        elif bucket['peak'] is None or point['value']>bucket['peak']['value']:
            bucket['peak']=point

    def add(self,at,value,interval=60):
        at=utc(at);interval=max(1,interval)
        if value is not None and (not math.isfinite(value) or value<0):value=None
        if value is not None:self.seen_value=True
        if self.previous is not None:
            before,_,previous_interval=self.previous
            if (at-before).total_seconds()>2.5*min(interval,previous_interval):
                elapsed=(at-before).total_seconds()
                self._append(before+timedelta(seconds=min(elapsed/2,previous_interval)),None)
        self._append(at,value);self.previous=(at,value,interval)

    def finish(self):
        if not self.seen_value:return []
        if self.previous is not None:
            at,value,interval=self.previous
            if value is not None and (self.end-at).total_seconds()>2.5*interval:
                self._append(at+timedelta(seconds=interval),None)
        if self.small is not None:return self.small
        result=[]
        for bucket in self.buckets.values():
            selected=[item for item in (bucket['peak'],bucket['first'],bucket['last']) if item is not None]
            result.extend({item['collected_at']:item for item in selected}.values())
        return sorted(result,key=lambda point:point['collected_at'])


def psu_key(name):
    match=re.search(r'(?:PSU|PS|Power[_ -]?Supply|PWR[_ -]?Supply)[_ -]*(\d+)',name,re.I)
    return f'PSU{int(match.group(1))}' if match else name


async def history(db,hours,network,now,gpu_interval=60):
    end=utc(now);start=end-timedelta(hours=hours)
    query=select(Server.id).where(func.lower(Server.name)!='poseidon').order_by(Server.id)
    if network!='all':query=query.where(Server.network==network)
    ids=list((await db.scalars(query)).all())
    keys=('power','cpu_temperature','gpu_temperature','network_rx','network_tx')
    series={server_id:{key:Series(start,end) for key in keys} for server_id in ids}
    psus={server_id:{} for server_id in ids}
    if not ids:return {'servers':{}}
    bounds=lambda column:(column>=utc_naive(start),column<=utc_naive(end))
    async def stream(query):
        result=await db.stream(query.execution_options(yield_per=1000))
        async for row in result:yield row

    power=PowerRawSample
    scopes={f'server:{server_id}':server_id for server_id in ids}
    async for scope,at,value,interval in stream(select(power.scope,power.observed_at,power.power_watts,power.interval_seconds).where(power.scope.in_(scopes),power.source=='dcmi',*bounds(power.observed_at)).order_by(power.observed_at,power.id)):
        series[scopes[scope]]['power'].add(at,value,interval)

    cpu=CpuTemperatureObservation
    async for server_id,at,value,interval in stream(select(cpu.server_id,func.max(cpu.observed_at),func.max(cpu.temperature),func.min(cpu.interval_seconds)).where(cpu.server_id.in_(ids),*bounds(cpu.observed_at)).group_by(cpu.server_id,cpu.round_id).order_by(func.max(cpu.observed_at))):
        series[server_id]['cpu_temperature'].add(at,value,interval)

    gpu=GpuMetric
    valid=case((gpu.temperature.between(1,150),gpu.temperature),else_=None)
    minute=func.strftime('%Y-%m-%dT%H:%M:00',gpu.collected_at)
    async for server_id,at,value in stream(select(gpu.server_id,func.max(gpu.collected_at),func.max(valid)).where(gpu.server_id.in_(ids),*bounds(gpu.collected_at)).group_by(gpu.server_id,minute).order_by(func.max(gpu.collected_at))):
        series[server_id]['gpu_temperature'].add(at,value,gpu_interval)

    net=NetworkMetric
    async for server_id,at,rx,tx,interval in stream(select(net.server_id,net.collected_at,net.network_rx_bytes_per_second,net.network_tx_bytes_per_second,net.interval_seconds).where(net.server_id.in_(ids),*bounds(net.collected_at)).order_by(net.collected_at,net.id)):
        series[server_id]['network_rx'].add(at,rx,interval);series[server_id]['network_tx'].add(at,tx,interval)

    psu=PowerPsuReading
    async for server_id,at,name,value,interval in stream(select(psu.server_id,psu.observed_at,psu.sensor_name,psu.power_watts,power.interval_seconds).join(power,and_(power.round_id==psu.round_id,power.scope==literal('server:')+cast(psu.server_id,String),power.source=='ac_input')).where(psu.server_id.in_(ids),psu.direction=='input',*bounds(psu.observed_at)).order_by(psu.observed_at,psu.id)):
        key=psu_key(name)
        if key not in psus[server_id]:psus[server_id][key]=Series(start,end)
        psus[server_id][key].add(at,value,interval)
    return {'servers':{str(server_id):{**{key:builder.finish() for key,builder in series[server_id].items()},'psu':{key:builder.finish() for key,builder in psus[server_id].items()}} for server_id in ids}}
