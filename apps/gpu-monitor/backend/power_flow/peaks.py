"""Compile immutable topology once; replay only complete simultaneous leaf sums.

No observations are cached across rounds. Shared node/cable/port leaf sets share
one calculation, and timestamp formatting happens only when returning peaks.
"""
import math
try:
    from ..telemetry_time import iso, utc_naive
except ImportError:  # pragma: no cover - direct execution fallback
    from telemetry_time import iso, utc_naive

from .evaluator import _get, normalize_slot


class CompiledPeaks:
    def __init__(self,graph):
        nodes={node.id:node for node in graph.nodes}
        children={key:[] for key in nodes};incoming={};outputs={}
        for edge in graph.edges:
            children[edge.source].append(edge.target);incoming[edge.target]=edge
            outputs[(edge.source,edge.source_port)]=edge
        voltages={}
        def voltage(key):
            if key not in voltages:
                node=nodes[key]
                voltages[key]=node.voltage_v if node.kind=='breaker' and node.voltage_v is not None else voltage(incoming[key].source) if key in incoming else graph.settings.voltage_v
            return voltages[key]
        self.leaves=[];leaf_indices={};self.bindings={}
        for node in graph.nodes:
            if node.kind not in ('psu','fixed'):continue
            index=len(self.leaves);leaf_indices[node.id]=index
            denominator=voltage(node.id)*(node.power_factor or graph.settings.power_factor)
            self.leaves.append((node.fixed_watts if node.kind=='fixed' else None,denominator))
            if node.kind=='psu':self.bindings[(node.server_id,node.psu_slot)]=index
        cache={}
        def descendants(key):
            if key not in cache:
                node=nodes[key]
                if node.kind in ('psu','fixed'):values=(leaf_indices[key],)
                elif node.kind=='server':values=tuple(leaf_indices[child.id] for child in graph.nodes if child.parent_id==key and child.kind=='psu')
                else:values=tuple(value for child in children[key] for value in descendants(child))
                cache[key]=values
            return cache[key]
        self.groups=[];interned={};self.targets=[];self.node_groups=set()
        def register(kind,key,indices):
            if indices not in interned:
                interned[indices]=len(self.groups);self.groups.append(indices)
            index=interned[indices];self.targets.append((kind,key,index))
            if kind=='nodes':self.node_groups.add(index)
        for node in graph.nodes:register('nodes',node.id,descendants(node.id))
        for edge in graph.edges:register('edges',edge.id,descendants(edge.target))
        for node in graph.nodes:
            for port in node.ports:
                edge=outputs.get((node.id,port.id)) if port.direction=='output' else incoming.get(node.id)
                register('ports',f'{node.id}:{port.id}',descendants(edge.target) if edge is not None else ())
        self.peaks=[[None,None,None,None] for _ in self.groups]
        self.max_age=graph.settings.max_age_seconds;self.max_skew=graph.settings.max_skew_seconds
        self.incomplete=False;self.sensor_indices={}

    def add_round(self,readings,at):
        at_naive=utc_naive(at)
        watts=[fixed for fixed,_ in self.leaves]
        observed=[None]*len(self.leaves);seen=set()
        for row in readings:
            server_id=_get(row,'server_id');sensor_name=_get(row,'sensor_name','');direction=_get(row,'direction')
            sensor=(server_id,sensor_name,direction)
            if sensor not in self.sensor_indices:
                slot=normalize_slot(sensor_name) if direction=='input' else None
                self.sensor_indices[sensor]=self.bindings.get((server_id,slot)) if slot else None
            index=self.sensor_indices[sensor]
            if index is None:continue
            if index in seen:
                watts[index]=None;observed[index]=None;continue
            seen.add(index)
            value=_get(row,'power_watts');timestamp=_get(row,'observed_at')
            if value is None or not math.isfinite(value) or value<0 or timestamp is None:continue
            timestamp=utc_naive(timestamp)
            if not 0<=(at_naive-timestamp).total_seconds()<=self.max_age:continue
            watts[index]=value;observed[index]=timestamp
        amps=[value/denominator if value is not None else None for value,(_,denominator) in zip(watts,self.leaves)]
        for index,indices in enumerate(self.groups):
            total_watts=total_amps=0;first=last=None;complete=True
            for leaf in indices:
                value=watts[leaf]
                if value is None:complete=False;break
                total_watts+=value;total_amps+=amps[leaf]
                timestamp=observed[leaf]
                if timestamp is not None:
                    if first is None or timestamp<first:first=timestamp
                    if last is None or timestamp>last:last=timestamp
            if complete and first is not None and (last-first).total_seconds()>self.max_skew:complete=False
            if not complete:
                if index in self.node_groups:self.incomplete=True
                continue
            peak=self.peaks[index]
            if peak[0] is None or total_watts>peak[0]:peak[0]=total_watts;peak[2]=at
            if peak[1] is None or total_amps>peak[1]:peak[1]=total_amps;peak[3]=at

    def apply(self,target):
        values=[dict(peak_watts=peak[0],peak_amps=peak[1],peak_watts_at=iso(peak[2]),peak_amps_at=iso(peak[3])) for peak in self.peaks]
        for kind,key,index in self.targets:target[kind][key].update(values[index])
