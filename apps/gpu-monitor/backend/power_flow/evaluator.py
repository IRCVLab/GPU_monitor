"""Pure same-round calculations. Every current is an estimate of leaf P/(V*PF).

Summed estimates do not represent measured upstream RMS current. An unknown
capacity stays unknown; incomplete readings preserve a known lower bound.
"""
import math
import re
try:
    from ..telemetry_time import iso, utc
except ImportError:  # pragma: no cover - direct execution fallback
    from telemetry_time import iso, utc


def normalize_slot(sensor_name):
    # BMC names also use underscores as separators (Pwr_PSU1_In). Python's
    # word boundary treats '_' as a word character, so normalize separators
    # only for matching; callers retain the original sensor name and direction.
    match=re.search(r'\bPSU?\s*#?\s*(\d+)\b',sensor_name.replace('_',' '),re.IGNORECASE)
    return f'PSU{int(match[1])}' if match and int(match[1])>0 else None


def _get(row,key,default=None):
    return row.get(key,default) if isinstance(row,dict) else getattr(row,key,default)


def input_readings(readings):
    """Ambiguous sensors resolving to one slot stay unknown, rather than double count."""
    values={}
    for row in readings:
        slot=normalize_slot(_get(row,'sensor_name',''))
        if _get(row,'direction')!='input' or slot is None:continue
        key=(_get(row,'server_id'),slot)
        values[key]=None if key in values else row
    return values


def evaluate(graph,readings,at):
    at=utc(at);nodes={node.id:node for node in graph.nodes}
    children={key:[] for key in nodes};incoming={};by_output={}
    for edge in graph.edges:
        children[edge.source].append(edge.target);incoming[edge.target]=edge
        by_output[(edge.source,edge.source_port)]=edge
    samples=input_readings(readings);supply={};leaves={};cache={}
    def voltage(key):
        if key in supply:return supply[key]
        node=nodes[key]
        value=node.voltage_v if node.kind=='breaker' and node.voltage_v is not None else voltage(incoming[key].source) if key in incoming else graph.settings.voltage_v
        supply[key]=value;return value
    def rooted(key):
        while key in incoming:key=incoming[key].source
        return nodes[key].kind=='breaker'
    def leaf(key):
        node=nodes[key];v=voltage(key);pf=node.power_factor or graph.settings.power_factor
        observed=None;missing=[];watts=None
        if node.kind=='fixed':watts=node.fixed_watts
        else:
            row=samples.get((node.server_id,node.psu_slot))
            if row is not None:
                observed=_get(row,'observed_at');value=_get(row,'power_watts')
                age=(at-utc(observed)).total_seconds() if observed is not None else -1
                if value is not None and math.isfinite(value) and value>=0 and 0<=age<=graph.settings.max_age_seconds:
                    watts=value
            if watts is None:missing=[f'{node.server_id}:{node.psu_slot}']
        return dict(watts=watts,amps=watts/(v*pf) if watts is not None else None,observed=utc(observed) if observed is not None and watts is not None else None,missing=missing)
    for key,node in nodes.items():
        if node.kind in ('psu','fixed'):leaves[key]=leaf(key)
    def descendants(key):
        if key in cache:return cache[key]
        node=nodes[key]
        if node.kind in ('psu','fixed'):values=[key]
        elif node.kind=='server':values=[child.id for child in graph.nodes if child.parent_id==key and child.kind=='psu']
        else:values=[value for child in children[key] for value in descendants(child)]
        cache[key]=values;return values
    def metric(key,leaf_ids,capacity=None,warning=None,unconnected=False):
        rows=[leaves[value] for value in leaf_ids];known=[row for row in rows if row['watts'] is not None]
        times=[row['observed'] for row in known if row['observed'] is not None]
        complete=len(rows)==len(known)
        skew=bool(times and (max(times)-min(times)).total_seconds()>graph.settings.max_skew_seconds)
        complete=complete and not skew
        # Samples outside the simultaneity tolerance cannot establish a combined
        # lower bound. Child branches remain independently usable, but this
        # aggregate must neither report their sum nor infer an overload from it.
        kw=sum(row['watts'] for row in known) if not skew else 0
        ka=sum(row['amps'] for row in known) if not skew else 0
        missing=[value for row in rows for value in row['missing']]
        if skew:missing+=['time_skew']
        v=voltage(key);pf=nodes[key].power_factor or graph.settings.power_factor
        if capacity is not None and ka>capacity:status='overload'
        elif not complete or capacity is None:status='unknown'
        elif warning is not None and ka>=warning:status='warning'
        else:status='estimated'
        if unconnected:status='unconnected'
        return dict(watts=kw if complete else None,amps=ka if complete else None,known_watts=kw,known_amps=ka,complete=complete,amps_estimated=True,
                    capacity_a=capacity,capacity_watts=capacity*v*pf if capacity is not None else None,utilization=ka/capacity if capacity is not None and complete else None,
                    status=status,voltage_v=v,power_factor=pf,observed_at=iso(max(times)) if times else None,missing_psus=missing,
                    peak_watts=None,peak_amps=None,peak_watts_at=None,peak_amps_at=None)
    node_metrics={key:metric(key,descendants(key),node.capacity_a,node.warning_a,unconnected=node.kind not in ('breaker','server') and not rooted(key)) for key,node in nodes.items()}
    edge_metrics={edge.id:metric(edge.target,descendants(edge.target),edge.capacity_a,edge.warning_a,unconnected=not rooted(edge.target)) for edge in graph.edges}
    ports={}
    for key,node in nodes.items():
        for port in node.ports:
            edge=by_output.get((key,port.id)) if port.direction=='output' else incoming.get(key)
            ids=descendants(edge.target) if edge is not None else []
            ports[f'{key}:{port.id}']=metric(key,ids,port.capacity_a,unconnected=edge is None or not rooted(key))
    return dict(nodes=node_metrics,edges=edge_metrics,ports=ports)
