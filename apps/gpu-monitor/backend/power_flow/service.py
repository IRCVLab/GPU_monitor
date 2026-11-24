"""Live and retained-raw measurements for Power Flow.

V1 deliberately clips actual peaks to the selected revision's effective period
and labels clipped windows partial. Scenario/draft previews replay raw observations
as projections. Raw rounds are paged, never reconstructed from PSU rollup maxima.
"""
from collections import defaultdict
import asyncio
from datetime import timedelta
import math
from sqlalchemy import select, exists, and_, or_
try:
    from .. import telemetry_time
    from ..telemetry_time import iso, utc, utc_naive
    from ..models import (HardwareRound,
                          PowerPsuReading, PowerPsuIdentity, PowerRawSample, Server)
except ImportError:  # pragma: no cover - direct execution fallback
    import telemetry_time
    from telemetry_time import iso, utc, utc_naive
    from models import (HardwareRound,
                        PowerPsuReading, PowerPsuIdentity, PowerRawSample, Server)
from .schema import FlowGraph
from .evaluator import evaluate, normalize_slot, input_readings
from .diagrams import get_diagram, find_revision
from .peaks import CompiledPeaks

RETENTION = timedelta(days=30)
PAGE_SIZE = 128


def completed_rounds():
    # Raw insertion and PSU insertion commit in one archive transaction. Requiring
    # its raw marker excludes unfinished/import-only HardwareRound placeholders.
    return exists(select(PowerRawSample.id).where(PowerRawSample.round_id==HardwareRound.id,PowerRawSample.source=='ac_input'))


async def latest_round(db,at,retention_start):
    return (await db.execute(select(HardwareRound.id,HardwareRound.collected_at).where(HardwareRound.collected_at<=utc_naive(at),HardwareRound.collected_at>=utc_naive(retention_start),completed_rounds()).order_by(HardwareRound.collected_at.desc(),HardwareRound.id.desc()).limit(1))).first()


async def readings_for(db,round_ids,server_ids=None):
    query=select(PowerPsuReading).where(PowerPsuReading.round_id.in_(round_ids),PowerPsuReading.direction=='input')
    if server_ids is not None:
        if not server_ids:return []
        query=query.where(PowerPsuReading.server_id.in_(server_ids))
    return (await db.execute(query)).scalars().all()


async def _peak_readings_for(db,round_ids,server_ids):
    if not server_ids:return []
    # Historical peaks need only scalar inputs, not thousands of ORM identities
    # and attribute tracking objects on every request.
    query=select(PowerPsuReading.round_id,PowerPsuReading.server_id,
                 PowerPsuReading.sensor_name,PowerPsuReading.direction,
                 PowerPsuReading.power_watts,PowerPsuReading.observed_at).where(
                     PowerPsuReading.round_id.in_(round_ids),
                     PowerPsuReading.server_id.in_(server_ids),
                     PowerPsuReading.direction=='input')
    return (await db.execute(query)).all()


async def catalog(db,at,interval_seconds,readings=None,max_age_seconds=75):
    if readings is None:
        latest=await latest_round(db,at,telemetry_time.now_utc()-RETENTION)
        readings=await readings_for(db,[latest.id]) if latest else []
    values=input_readings(readings)
    identities=(await db.execute(select(PowerPsuIdentity).where(PowerPsuIdentity.direction=='input'))).scalars().all()
    names=defaultdict(set);active_names=defaultdict(set)
    for row in identities:
        slot=normalize_slot(row.sensor_name)
        if slot:names[(row.server_id,slot)].add(row.sensor_name)
    for row in readings:
        slot=normalize_slot(row.sensor_name)
        if slot:
            names[(row.server_id,slot)].add(row.sensor_name)
            active_names[(row.server_id,slot)].add(row.sensor_name)
    servers=(await db.execute(select(Server).where(Server.network=='internal').order_by(Server.display_order,Server.id))).scalars().all()
    results=[]
    for server in servers:
        psus=[]
        for (server_id,slot),sensor_names in sorted(names.items(),key=lambda entry:(entry[0][0],int(entry[0][1][3:]))):
            if server_id!=server.id:continue
            row=values.get((server_id,slot))
            watts=row.power_watts if row is not None else None
            status=row.status if row is not None else 'missing'
            if len(active_names[(server_id,slot)])>1:watts=None;status='ambiguous'
            if watts is not None and (not math.isfinite(watts) or watts<0):watts=None;status='invalid'
            if row is not None and not 0<=(utc(at)-utc(row.observed_at)).total_seconds()<=max_age_seconds:watts=None;status='stale'
            sensor_name=row.sensor_name if row is not None else sorted(sensor_names)[0]
            psus.append(dict(server_id=server.id,server_name=server.name,slot=slot,sensor_name=sensor_name,watts=watts,observed_at=iso(row.observed_at) if row is not None else None,status=status))
        results.append(dict(server_id=server.id,name=server.name,psus=psus))
    return dict(servers=results,interval_seconds=interval_seconds)


def _merge_peaks(target,sample,at):
    for group in ('nodes','edges','ports'):
        for key,metric in sample[group].items():
            if not metric['complete']:continue
            result=target[group][key]
            for value in ('watts','amps'):
                peak=f'peak_{value}'
                if result[peak] is None or metric[value]>result[peak]:
                    result[peak]=metric[value];result[f'{peak}_at']=iso(at)


async def evaluate_state(db,graph,at,hours,*,diagram=None,revision=None,interval_seconds=60,include_peaks=True):
    at=utc(at);retention_start=telemetry_time.now_utc()-RETENTION
    projection=diagram is None or bool(diagram.is_scenario)
    base=dict(diagram_id=diagram.id if diagram else None,version=revision.version if revision else None,at=iso(at),graph=graph.model_dump() if graph else None,
              effective_from=iso(revision.effective_from) if revision else None,nodes={},edges={},ports={},unmapped_psus=[],history_status='projection' if projection else 'available',is_scenario=bool(diagram.is_scenario) if diagram else True,peaks_included=include_peaks)
    if graph is None:
        base.update(history_status='before_topology',effective_from=None,version=None);return base
    if at<retention_start:
        base['history_status']='out_of_retention';return base
    latest=await latest_round(db,at,retention_start)
    readings=await readings_for(db,[latest.id]) if latest else []
    current=evaluate(graph,readings,at);base.update(current)
    all_psus=await catalog(db,at,interval_seconds,readings,max_age_seconds=graph.settings.max_age_seconds)
    nodes={node.id:node for node in graph.nodes};incoming={edge.target:edge.source for edge in graph.edges}
    connected=set()
    for node in graph.nodes:
        if node.kind!='psu':continue
        key=node.id
        while key in incoming:key=incoming[key]
        if nodes[key].kind=='breaker':connected.add((node.server_id,node.psu_slot))
    base['unmapped_psus']=[psu for server in all_psus['servers'] for psu in server['psus'] if (psu['server_id'],psu['slot']) not in connected]
    start=at-timedelta(hours=hours)
    lower=max(start,retention_start)
    if not projection:lower=max(lower,utc(revision.effective_from))
    # Current actual revision is valid through selected at. Older revisions in
    # the requested window are intentionally omitted and reflected as partial.
    clipped=lower>start
    if not include_peaks:
        # Current samples cannot establish coverage of an unscanned window.
        if not projection:base['history_status']='pending'
        return base
    count,incomplete=await _scan_peaks(db,graph,lower,at,base)
    if not projection:
        base['history_status']='no_data' if not count else 'partial' if clipped or incomplete else 'available'
    return base


async def _scan_peaks(db,graph,lower,at,target):
    compiled=CompiledPeaks(graph)
    cursor=None;count=0
    server_ids={node.server_id for node in graph.nodes if node.kind=='psu'}
    while True:
        query=select(HardwareRound.id,HardwareRound.collected_at).where(HardwareRound.collected_at>=utc_naive(lower),HardwareRound.collected_at<=utc_naive(at),completed_rounds())
        if cursor is not None:
            timestamp,round_id=cursor
            query=query.where(or_(HardwareRound.collected_at>timestamp,and_(HardwareRound.collected_at==timestamp,HardwareRound.id>round_id)))
        rounds=(await db.execute(query.order_by(HardwareRound.collected_at,HardwareRound.id).limit(PAGE_SIZE))).all()
        if not rounds:break
        batch=defaultdict(list)
        for row in await _peak_readings_for(db,[row.id for row in rounds],server_ids):batch[row.round_id].append(row)
        for row in rounds:
            compiled.add_round(batch[row.id],row.collected_at);count+=1
            if count%32==0:await asyncio.sleep(0)
        cursor=(rounds[-1].collected_at,rounds[-1].id)
        await asyncio.sleep(0)
        if len(rounds)<PAGE_SIZE:break
    compiled.apply(target)
    return count,compiled.incomplete


async def diagram_state(db,diagram_id,at,hours,interval_seconds,include_peaks=True):
    diagram=await get_diagram(db,diagram_id)
    revision = await find_revision(db, diagram, at=at)
    graph=FlowGraph.model_validate(revision.graph) if revision else None
    return await evaluate_state(db,graph,at,hours,diagram=diagram,revision=revision,interval_seconds=interval_seconds,include_peaks=include_peaks)
