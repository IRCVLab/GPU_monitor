"""Measured power logging. No source substitution or assumed zero readings.

DCMI is platform power; ac_input is the sum of measured PSU AC inputs. PSU
outputs are archived individually and never included in AC or DCMI totals.
Network samples use the latest actual observation time in each polling cohort;
server samples retain their own observation times. These are polling-round
aggregates, not synchronized electrical meter measurements.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from sqlalchemy import select, delete, func
from sqlalchemy.dialects.sqlite import insert
try:
    from .models import HardwarePowerObservation, PowerRawSample, PowerPsuReading, PowerPsuIdentity, PowerRollup, PowerCheckpoint
except ImportError:
    from models import HardwarePowerObservation, PowerRawSample, PowerPsuReading, PowerPsuIdentity, PowerRollup, PowerCheckpoint


def utc(at):
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def naive(at): return utc(at).replace(tzinfo=None)


def parse_at(at): return utc(datetime.fromisoformat(at.replace('Z','+00:00')))


def valid_power(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError,ValueError): return None


def psu_readings(snapshot):
    result=[]
    for sensor in snapshot.get('sensors',[]):
        name=sensor.get('name','')
        if sensor.get('kind') != 'power' or not re.search(r'(?:psu|ps)[_ -]?\d+|power.?supply|pwr.?supply',name,re.I): continue
        normalized=re.sub(r'[_-]+',' ',name)
        direction='input' if re.search(r'\b(?:in|input)\b',normalized,re.I) else 'output' if re.search(r'\b(?:out|output)\b',normalized,re.I) else None
        if direction:
            # Unknown status and unavailable values remain missing. Warning and
            # critical numeric measurements are still actual observations.
            value=valid_power(sensor.get('value'))
            if sensor.get('status','').lower() in ('na','n/a','ns','disabled','not present','unavailable'): value=None
            result.append({'name':name,'direction':direction,'power_watts':value,'status':sensor.get('status')})
    return result


def psu_total(readings,direction):
    values=[item['power_watts'] for item in readings if item['direction']==direction]
    return sum(values) if values and all(value is not None for value in values) else None


def as_sample(row):
    return {'at':utc(row.observed_at),'power_watts':row.power_watts,'cohort':row.cohort,'interval':row.interval_seconds}


def json_sample(sample):
    return {**sample,'at':utc(sample['at']).isoformat()}


def segments(samples,start,end):
    """Actual adjacent observations only; interpolate solely to clip boundaries."""
    for left,right in zip(samples,samples[1:]):
        delta=(right['at']-left['at']).total_seconds()
        if delta <= 0 or delta > 2.5*min(left['interval'],right['interval']): continue
        if left['power_watts'] is None or right['power_watts'] is None or left['cohort'] != right['cohort']: continue
        a=max(start,left['at']); b=min(end,right['at'])
        if b<=a: continue
        slope=(right['power_watts']-left['power_watts'])/delta
        wa=left['power_watts']+slope*(a-left['at']).total_seconds()
        wb=left['power_watts']+slope*(b-left['at']).total_seconds()
        yield a,b,wa,wb,left,right


def summarize(samples,start,end):
    start,end=utc(start),utc(end)
    energy=seconds=0.0
    for a,b,wa,wb,_,_ in segments(samples,start,end):
        duration=(b-a).total_seconds();seconds+=duration;energy+=(wa+wb)*0.5*duration/3600
    observed=[sample['power_watts'] for sample in samples if start<=sample['at']<=end and sample['power_watts'] is not None]
    return {'energy_wh':energy,'covered_seconds':seconds,'coverage_ratio':seconds/(end-start).total_seconds() if end>start else 0,
            'time_weighted_avg_watts':energy*3600/seconds if seconds else None,
            'min_watts':min(observed) if observed else None,'max_watts':max(observed) if observed else None}


async def record_round(db,round_id,servers,snapshots,at,interval):
    identities=(await db.execute(select(PowerPsuIdentity))).scalars().all()
    expected=defaultdict(set)
    for identity in identities: expected[identity.server_id].add((identity.sensor_name,identity.direction))
    rows=[];server_samples={};signatures={}
    for server in servers:
        snapshot=snapshots.get(str(server.id),{})
        observed=parse_at(snapshot['collected_at']) if snapshot.get('collected_at') else utc(at)
        readings=psu_readings(snapshot)
        present={(item['name'],item['direction']) for item in readings}
        for name,direction in present-expected[server.id]:
            db.add(PowerPsuIdentity(server_id=server.id,sensor_name=name,direction=direction))
        for name,direction in expected[server.id]-present:
            readings.append({'name':name,'direction':direction,'power_watts':None,'status':'missing'})
        for item in readings:
            db.add(PowerPsuReading(round_id=round_id,server_id=server.id,sensor_name=item['name'],direction=item['direction'],power_watts=item['power_watts'],status=item['status'],observed_at=naive(observed)))
            rows.append(dict(round_id=round_id,scope=f"psu:{server.id}:{item['direction']}:{item['name']}",source='ac_input' if item['direction']=='input' else 'psu_output',observed_at=naive(observed),power_watts=item['power_watts'],
                             cohort=str(server.id),expected_count=1,server_count=int(item['power_watts'] is not None),interval_seconds=interval))
        values={'dcmi':valid_power(snapshot.get('power_watts')),'ac_input':psu_total(readings,'input')}
        server_samples[server.id]=(observed,values)
        signatures[server.id]={'dcmi':str(server.id),'ac_input':str(server.id)+':'+','.join(sorted(item['name'] for item in readings if item['direction']=='input'))}
        for source,value in values.items():
            rows.append(dict(round_id=round_id,scope=f'server:{server.id}',source=source,observed_at=naive(observed),power_watts=value,
                             cohort=signatures[server.id][source],expected_count=1,server_count=int(value is not None),interval_seconds=interval))
    for network in ('internal','external','all'):
        cohort=[server for server in servers if network=='all' or server.network==network]
        if not cohort: continue
        timestamp=max(server_samples[server.id][0] for server in cohort)
        for source in ('dcmi','ac_input'):
            membership='|'.join(signatures[server.id][source] for server in sorted(cohort,key=lambda server:server.id))
            values=[server_samples[server.id][1][source] for server in cohort]
            count=sum(value is not None for value in values)
            rows.append(dict(round_id=round_id,scope=f'network:{network}',source=source,observed_at=naive(timestamp),power_watts=sum(values) if count==len(values) else None,
                             cohort=membership,expected_count=len(values),server_count=count,interval_seconds=interval))
    if rows:
        for offset in range(0,len(rows),50):
            await db.execute(insert(PowerRawSample).values(rows[offset:offset+50]).on_conflict_do_nothing(index_elements=['round_id','scope','source']))
        await db.flush()
        await refresh_rollups(db,min(row['observed_at'] for row in rows),max(row['observed_at'] for row in rows))


def bucket_floor(at,width):
    return datetime.fromtimestamp(int(utc(at).timestamp()//width)*width,tz=timezone.utc)


def build_buckets(samples,width):
    buckets={}
    def get(at):
        key=bucket_floor(at,width)
        if key not in buckets:
            buckets[key]={'energy_wh':0.0,'observed_seconds':0.0,'sample_count':0,'min_watts':None,'max_watts':None,'first_sample':None,'last_sample':None,'start_edge':None,'end_edge':None}
        return buckets[key]
    for sample in samples:
        row=get(sample['at']);row['sample_count']+=1
        value=sample['power_watts']
        if value is not None:
            row['min_watts']=value if row['min_watts'] is None else min(value,row['min_watts'])
            row['max_watts']=value if row['max_watts'] is None else max(value,row['max_watts'])
        row['first_sample']=row['first_sample'] or json_sample(sample);row['last_sample']=json_sample(sample)
    if not samples:return buckets
    for a,b,wa,wb,left,right in segments(samples,samples[0]['at'],samples[-1]['at']):
        position=a
        while position<b:
            end=min(b,bucket_floor(position,width)+timedelta(seconds=width))
            fraction1=(position-a).total_seconds()/(b-a).total_seconds()
            fraction2=(end-a).total_seconds()/(b-a).total_seconds()
            p1=wa+(wb-wa)*fraction1;p2=wa+(wb-wa)*fraction2
            row=get(position);duration=(end-position).total_seconds()
            row['energy_wh']+=(p1+p2)*0.5*duration/3600;row['observed_seconds']+=duration
            edge=[json_sample(left),json_sample(right)]
            row['start_edge']=row['start_edge'] or edge;row['end_edge']=edge
            position=end
    return buckets


async def refresh_rollups(db,start,end):
    """Rebuild affected UTC buckets, replacing totals rather than adding twice."""
    span=2.5*(await db.scalar(select(func.max(PowerRawSample.interval_seconds))) or 60)
    low=utc(start)-timedelta(seconds=span);high=utc(end)+timedelta(seconds=span)
    query_low=bucket_floor(low,3600)-timedelta(seconds=span)
    query_high=bucket_floor(high,3600)+timedelta(seconds=3600+span)
    rows=(await db.execute(select(PowerRawSample).where(PowerRawSample.observed_at>=naive(query_low),PowerRawSample.observed_at<=naive(query_high)).order_by(PowerRawSample.observed_at,PowerRawSample.id))).scalars().all()
    series=defaultdict(list)
    for row in rows:series[(row.scope,row.source)].append(as_sample(row))
    checkpoints={(row.scope,row.source):row for row in (await db.execute(select(PowerCheckpoint))).scalars()}
    output=[]
    for (scope,source),samples in series.items():
        checkpoint=checkpoints.get((scope,source))
        if checkpoint and checkpoint.anchor:
            anchor={**checkpoint.anchor,'at':parse_at(checkpoint.anchor['at'])}
            if anchor['at']<samples[0]['at']:samples.insert(0,anchor)
        for resolution,width in (('minute',60),('hour',3600)):
            for at,values in build_buckets(samples,width).items():
                if at<bucket_floor(low,width) or at>bucket_floor(high,width):continue
                if checkpoint and at<utc(checkpoint.closed_before):continue
                output.append(dict(scope=scope,source=source,resolution=resolution,bucket_start=naive(at),**values))
    if output:
        # Bound parameter count across SQLite builds.
        for offset in range(0,len(output),50):
            statement=insert(PowerRollup).values(output[offset:offset+50])
            await db.execute(statement.on_conflict_do_update(index_elements=['scope','source','resolution','bucket_start'],set_={key:getattr(statement.excluded,key) for key in output[0] if key not in ('scope','source','resolution','bucket_start')}))


async def prune(db,now):
    """Commit rollups/checkpoints and raw deletion atomically via caller transaction."""
    cutoff=bucket_floor(utc(now)-timedelta(days=30),3600)
    oldest=await db.scalar(select(func.min(PowerRawSample.observed_at)).where(PowerRawSample.observed_at<naive(cutoff)))
    if oldest is not None:
        latest_old=await db.scalar(select(func.max(PowerRawSample.observed_at)).where(PowerRawSample.observed_at<naive(cutoff)))
        span=2.5*(await db.scalar(select(func.max(PowerRawSample.interval_seconds))) or 60)
        refresh_end=min(cutoff,utc(latest_old)+timedelta(seconds=span))
        cursor=utc(oldest)
        while cursor<refresh_end:
            end=min(cursor+timedelta(days=1),refresh_end)
            await refresh_rollups(db,cursor,end)
            cursor=end
        ranked=select(PowerRawSample.id.label('id'),func.row_number().over(partition_by=(PowerRawSample.scope,PowerRawSample.source),order_by=(PowerRawSample.observed_at.desc(),PowerRawSample.id.desc())).label('rank')).where(PowerRawSample.observed_at<naive(cutoff)).subquery()
        old=(await db.execute(select(PowerRawSample).where(PowerRawSample.id.in_(select(ranked.c.id).where(ranked.c.rank==1))))).scalars().all()
        latest={}
        for row in old:latest[(row.scope,row.source)]=row
        for (scope,source),row in latest.items():
            checkpoint=await db.get(PowerCheckpoint,(scope,source))
            if checkpoint is None:
                checkpoint=PowerCheckpoint(scope=scope,source=source);db.add(checkpoint)
            checkpoint.closed_before=naive(cutoff);checkpoint.anchor=json_sample(as_sample(row))
        await db.flush()
        await db.execute(delete(PowerRawSample).where(PowerRawSample.observed_at<naive(cutoff)))
    await db.execute(delete(PowerPsuReading).where(PowerPsuReading.observed_at<naive(cutoff)))
    await db.execute(delete(PowerRollup).where(PowerRollup.resolution=='minute',PowerRollup.bucket_start<naive(utc(now)-timedelta(days=365))))
    return naive(cutoff)


async def history(db,hours,network,source,now,interval):
    end=utc(now);start=end-timedelta(hours=hours)
    span=timedelta(seconds=9000) # max allowed interval (3600 s) x 2.5
    rows=(await db.execute(select(PowerRawSample).where(PowerRawSample.scope==f'network:{network}',PowerRawSample.source==source,
        PowerRawSample.observed_at>=naive(start-span),PowerRawSample.observed_at<=naive(end)).order_by(PowerRawSample.observed_at,PowerRawSample.id))).scalars().all()
    summary=summarize([as_sample(row) for row in rows],start,end);summary['source']=source
    peaks=[row for row in rows if row.observed_at>=naive(start) and row.power_watts is not None]
    summary['peak_at']=utc(max(peaks,key=lambda row:row.power_watts).observed_at).isoformat() if peaks else None
    observed_subtotals = {}
    if source == 'dcmi' and any(0 < row.server_count < row.expected_count for row in rows):
        observation = HardwarePowerObservation
        subtotals = select(PowerRawSample.round_id, func.sum(observation.power_watts)).join(
            observation, observation.round_id == PowerRawSample.round_id
        ).where(
            PowerRawSample.scope == f'network:{network}', PowerRawSample.source == source,
            PowerRawSample.observed_at >= naive(start), PowerRawSample.observed_at <= naive(end),
            PowerRawSample.server_count > 0, PowerRawSample.server_count < PowerRawSample.expected_count,
            observation.power_watts >= 0,
        )
        if network != 'all':
            subtotals = subtotals.where(observation.network == network)
        subtotals = subtotals.group_by(PowerRawSample.round_id).having(
            func.count(observation.power_watts) == func.max(PowerRawSample.server_count)
        )
        observed_subtotals = dict((await db.execute(subtotals)).all())
    points=[]
    previous_row=None
    for row in rows:
        if row.observed_at<naive(start):continue
        if previous_row is not None:
            previous=utc(previous_row.observed_at)
            elapsed=(utc(row.observed_at)-previous).total_seconds()
            valid=bool(list(segments([as_sample(previous_row),as_sample(row)],previous,utc(row.observed_at))))
            # Include an explicit break for every rejected adjacency, including
            # equal-sized cohort changes and historical interval changes.
            elapsed_gap=elapsed>2.5*min(previous_row.interval_seconds,row.interval_seconds)
            if elapsed>0 and (elapsed_gap or (not valid and previous_row.power_watts is not None and row.power_watts is not None)):
                marker=previous+timedelta(seconds=min(elapsed/2,previous_row.interval_seconds))
                points.append(dict(collected_at=marker.isoformat(),power_watts=None,server_count=0,expected_count=row.expected_count,complete=False))
        points.append(dict(collected_at=utc(row.observed_at).isoformat(),power_watts=row.power_watts,server_count=row.server_count,expected_count=row.expected_count,complete=row.power_watts is not None))
        if row.round_id in observed_subtotals:
            points[-1]['observed_watts'] = observed_subtotals[row.round_id]
        previous_row=row
    started=await db.scalar(select(func.min(PowerRawSample.observed_at)).where(PowerRawSample.scope==f'network:{network}',PowerRawSample.source==source))
    return {'points':points,'started_at':utc(started).isoformat() if started else None,'summary':summary}


async def read_rollups(db,resolution,days,network,source,now,after=None):
    width=60 if resolution=='minute' else 3600
    # Return true buckets with their own boundaries, including a current partial
    # bucket whose observed_seconds tells clients the actual measured coverage.
    start=utc(now)-timedelta(days=days)
    if after is not None: start=max(start,utc(after))
    rows=(await db.execute(select(PowerRollup).where(PowerRollup.scope==f'network:{network}',PowerRollup.source==source,
        PowerRollup.resolution==resolution,PowerRollup.bucket_start>=naive(start),PowerRollup.bucket_start<=naive(utc(now))).order_by(PowerRollup.bucket_start).limit(10001))).scalars().all()
    # Minute queries can span one year; paginate using actual timestamp cursor.
    truncated=len(rows)>10000;rows=rows[:10000]
    buckets=[]
    for row in rows:
        buckets.append(dict(bucket_start=utc(row.bucket_start).isoformat(),bucket_end=(utc(row.bucket_start)+timedelta(seconds=width)).isoformat(),energy_wh=row.energy_wh,
            observed_seconds=row.observed_seconds,sample_count=row.sample_count,min_watts=row.min_watts,max_watts=row.max_watts,
            time_weighted_avg_watts=row.energy_wh*3600/row.observed_seconds if row.observed_seconds else None,coverage_ratio=row.observed_seconds/width))
    return {'resolution':resolution,'source':source,'network':network,'buckets':buckets,'truncated':truncated,
            'next_after':buckets[-1]['bucket_end'] if truncated and buckets else None}


def downsample_history(points,limit=1500):
    """Preserve actual peaks/timestamps and missing markers for capacity planning."""
    if len(points)<=limit:return points
    first=parse_at(points[0]['collected_at']).timestamp();last=parse_at(points[-1]['collected_at']).timestamp()
    width=max(1,(last-first)/(limit//4-1))
    buckets=defaultdict(list)
    for point in points:
        buckets[int((parse_at(point['collected_at']).timestamp()-first)//width)].append(point)
    output=[]
    for bucket in buckets.values():
        valid=[point for point in bucket if point['complete'] and point['power_watts'] is not None]
        missing=[point for point in bucket if not point['complete'] or point['power_watts'] is None]
        selected=[]
        if valid:selected.append(max(valid,key=lambda point:point['power_watts']))
        if missing:
            selected.append(missing[0])
            if missing[-1] is not missing[0]:selected.append(missing[-1])
            partial = [point for point in missing if point.get('observed_watts') is not None]
            if partial:
                peak = max(partial, key=lambda point: point['observed_watts'])
                if peak not in selected:
                    selected.append(peak)
        output.extend(sorted(selected,key=lambda point:point['collected_at']))
    return output
