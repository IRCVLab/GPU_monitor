"""Bounded transient scan observations, independent of last-good snapshots."""
from datetime import datetime, timezone
import math
import re
from collector.transport import TransportError, ACTIVE_SCAN_STATES

NUMBERS = ('started_unix','finished_unix','updated_unix','scanned_files','scanned_dirs','scanned_bytes','roots_completed','roots_total','last_finished_unix','last_duration_sec','estimate_duration_sec')
STATES = {'idle','running','complete','partial','failed','unknown'}
PHASES = {'preparing','scanning','publishing'}

def empty_status():
    return dict.fromkeys((*NUMBERS,'phase','current_root','observed_unix','next_run_unix','error_code','generation','snapshot_generation'), None) | {'state':'unknown','timer_state':'unknown','progress_available':False}

def validate_lifecycle(value):
    if not isinstance(value, dict):
        raise TransportError('BAD_TELEMETRY','telemetry must be an object')
    result = {}
    for key in NUMBERS:
        v = value.get(key)
        if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0 <= v < 10**18):
            raise TransportError('BAD_TELEMETRY','invalid telemetry counter')
        if key in value: result[key] = v
    for key, limit in (('current_root',4096),('generation',256),('error_code',128),('server_id',128),('run_id',64)):
        v = value.get(key)
        if v is not None and (not isinstance(v,str) or len(v)>limit or any(ord(c)<32 for c in v)):
            raise TransportError('BAD_TELEMETRY','invalid telemetry text')
        if key in value: result[key] = v
    for key, allowed in (('state',STATES),('phase',PHASES)):
        if key in value:
            if value[key] is not None and value[key] not in allowed:
                raise TransportError('BAD_TELEMETRY','invalid telemetry state')
            result[key] = value[key]
    return result

def parse_timer(text, *, remote_unix=None, remote_monotonic=None):
    if not isinstance(text,str) or len(text)>8192:
        raise TransportError('BAD_TIMER','timer output is malformed')
    props = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
    state = props.get('ActiveState','unknown')
    result = {'timer_state':state if state in {'active','inactive','failed'} else 'unknown','next_run_unix':None}
    if state != 'active': return result
    raw = props.get('NextElapseUSecRealtime','')
    try:
        result['next_run_unix'] = int(datetime.strptime(raw,'%a %Y-%m-%d %H:%M:%S %Z').replace(tzinfo=timezone.utc).timestamp())
    except (ValueError,OverflowError): pass
    # systemctl renders timespans as e.g. "3d 2h 14min 5s 200ms".
    mono = props.get('NextElapseUSecMonotonic','')
    units = {'us':.000001,'ms':.001,'s':1,'min':60,'h':3600,'d':86400,'w':604800}
    tokens = re.findall(r'(\d+(?:\.\d+)?)\s*(us|ms|min|s|h|d|w)',mono)
    residue = re.sub(r'(\d+(?:\.\d+)?)\s*(us|ms|min|s|h|d|w)','',mono).strip()
    if tokens and not residue and remote_unix is not None and remote_monotonic is not None:
        deadline = sum(float(n)*units[u] for n,u in tokens)
        candidate = int(remote_unix + deadline - remote_monotonic)
        if candidate > remote_unix:
            result['next_run_unix'] = min(result['next_run_unix'] or candidate,candidate)
    if remote_unix is not None and result['next_run_unix'] is not None and result['next_run_unix'] <= remote_unix:
        result['next_run_unix'] = None
    return result

def merge_status(lifecycle, progress, active, timer, now):
    life = validate_lifecycle(lifecycle)
    prog = validate_lifecycle(progress)
    result = empty_status()
    result.update(life)
    result.update(timer)
    result['observed_unix'] = now
    if active in ACTIVE_SCAN_STATES:
        result['state'] = 'running'
        if life.get('state') != 'running':
            for key in ('started_unix','finished_unix','phase','current_root','generation','updated_unix', 'error_code', 'scanned_files','scanned_dirs','scanned_bytes','roots_completed','roots_total'):
                result[key] = None
        else:
            if life.get('run_id') and prog.get('run_id') == life.get('run_id') and prog.get('updated_unix',0) >= life.get('started_unix',0):
                result.update({k:v for k,v in prog.items() if k not in {'state','started_unix','finished_unix','generation'}})
                result['progress_available'] = True
            if result.get('updated_unix') is not None and now - result['updated_unix'] > 30:
                result['error_code'] = 'STALE_PROGRESS'
                result['progress_available'] = False
    elif active == 'failed':
        result['state'] = 'failed'
        result['error_code'] = life.get('error_code') or 'SERVICE_FAILED'
    elif life.get('state') == 'running':
        result['state'] = 'unknown'; result['error_code'] = 'SCAN_INTERRUPTED'
    elif not life:
        result['state'] = 'idle' if active == 'inactive' else 'unknown'
    if not life:
        result.pop('estimate_duration_sec',None)
    result.pop('server_id',None); result.pop('run_id',None)
    return result

# Fixed script: no browser/inventory values are interpolated into remote commands or paths.
REMOTE_OBSERVATION_SCRIPT = r'''import json, os, re, stat, subprocess, time

def read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): return {'invalid': True}
            data = f.read(16385)
        if len(data)>16384: return {'invalid': True}
        return json.loads(data)
    except FileNotFoundError: return {}
    except Exception: return {'invalid': True}

def show(unit, properties):
    try:
        p = subprocess.run(['/usr/bin/systemctl','show',unit,'--property='+properties], env={'LC_ALL':'C','TZ':'UTC','PATH':'/usr/bin:/bin'}, capture_output=True, text=True, timeout=3)
        return p.stdout[:8192] if p.returncode == 0 else ''
    except Exception: return ''

life = read('/var/lib/storage-viz/scan-lifecycle.json')
progress = read('/run/storage-viz/scan-progress.json')
timer = show('storage-viz-scan.timer','ActiveState,NextElapseUSecRealtime,NextElapseUSecMonotonic')
# Human-readable systemctl spans use month/year units on long-running hosts.
# Ask D-Bus for the exact integer instead of guessing their duration.
mono = next((line.split('=',1)[1] for line in timer.splitlines() if line.startswith('NextElapseUSecMonotonic=')), '')
if 'month' in mono or 'y' in mono:
    try:
        p = subprocess.run(['/usr/bin/busctl','--system','get-property','org.freedesktop.systemd1','/org/freedesktop/systemd1/unit/storage_2dviz_2dscan_2etimer','org.freedesktop.systemd1.Timer','NextElapseUSecMonotonic'], capture_output=True, text=True, timeout=2)
        match = re.fullmatch(r't ([0-9]{1,20})\s*', p.stdout) if p.returncode == 0 else None
        if match and 0 < int(match.group(1)) < 18446744073709551615:
            timer = '\n'.join(line for line in timer.splitlines() if not line.startswith('NextElapseUSecMonotonic=')) + '\nNextElapseUSecMonotonic=' + match.group(1) + 'us'
    except Exception:
        pass
service = show('storage-viz-scan.service','ActiveState')
print(json.dumps({'lifecycle':life,'progress':progress,'service':service,'timer':timer,'remote_unix':time.time(),'remote_monotonic':time.clock_gettime(time.CLOCK_MONOTONIC)}))
'''
