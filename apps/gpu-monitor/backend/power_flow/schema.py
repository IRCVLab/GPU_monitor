"""Bounded wire contract. Values are single-phase assumptions, never live controls."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PowerFactor = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Label = Annotated[str, Field(max_length=200)]
Coordinate = Annotated[float, Field(ge=-1_000_000, le=1_000_000, allow_inf_nan=False)]

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')

class FlowPort(Contract):
    id: Identifier
    label: Label = ''
    direction: Literal['input','output']
    capacity_a: Positive | None = None

class FlowNode(Contract):
    id: Identifier
    kind: Literal['breaker','outlet','strip','server','psu','fixed']
    label: Label = ''
    x: Coordinate = 0
    y: Coordinate = 0
    parent_id: Identifier | None = None
    rack: Annotated[str, Field(min_length=1,max_length=64)] | None = None
    capacity_a: Positive | None = None
    warning_a: Positive | None = None
    voltage_v: Positive | None = None
    power_factor: PowerFactor | None = None
    fixed_watts: Annotated[float, Field(ge=0,allow_inf_nan=False)] | None = None
    server_id: Annotated[int, Field(gt=0)] | None = None
    psu_slot: Annotated[str, Field(pattern=r'^PSU[1-9][0-9]*$',max_length=24)] | None = None
    ports: list[FlowPort] = Field(default_factory=list,max_length=32)

class FlowEdge(Contract):
    id: Identifier
    label: Label = ''
    source: Identifier
    source_port: Identifier
    target: Identifier
    target_port: Identifier
    capacity_a: Positive | None = None
    warning_a: Positive | None = None

class FlowSettings(Contract):
    phase: Literal['single'] = 'single'
    voltage_v: Positive = 220
    power_factor: PowerFactor = 1
    max_age_seconds: Annotated[float,Field(gt=0,le=3600,allow_inf_nan=False)] = 75
    max_skew_seconds: Annotated[float,Field(ge=0,le=3600,allow_inf_nan=False)] = 30

class FlowGraph(Contract):
    nodes: list[FlowNode] = Field(default_factory=list,max_length=250)
    edges: list[FlowEdge] = Field(default_factory=list,max_length=500)
    settings: FlowSettings = Field(default_factory=FlowSettings)

    @model_validator(mode='after')
    def topology(self):
        nodes={node.id:node for node in self.nodes}
        if len(nodes)!=len(self.nodes):raise ValueError('Duplicate node id')
        if len({edge.id for edge in self.edges})!=len(self.edges):raise ValueError('Duplicate edge id')
        sockets={};bindings=set()
        for node in self.nodes:
            sockets[node.id]={port.id:port for port in node.ports}
            if len(sockets[node.id])!=len(node.ports):raise ValueError('Duplicate port id')
            inputs=sum(port.direction=='input' for port in node.ports)
            outputs=len(node.ports)-inputs
            if node.rack is not None and node.kind!='server':raise ValueError('Only server groups have rack metadata')
            if node.kind=='server' and node.ports:raise ValueError('Server groups have no electrical ports')
            if node.kind=='breaker' and inputs:raise ValueError('Breakers are supply roots')
            if node.kind in ('psu','fixed') and (inputs!=1 or outputs):raise ValueError('Loads need exactly one input and no outputs')
            if node.kind in ('outlet','strip') and inputs!=1:raise ValueError('Distribution nodes need exactly one input')
            if node.parent_id is not None:
                parent=nodes.get(node.parent_id)
                if node.kind!='psu' or parent is None or parent.kind!='server':raise ValueError('Only PSU nodes may belong to a server group')
                if parent.server_id is not None and parent.server_id!=node.server_id:raise ValueError('PSU binding differs from its server group')
            if node.kind=='psu':
                if node.server_id is None or node.psu_slot is None:raise ValueError('PSU requires server_id and psu_slot')
                binding=(node.server_id,node.psu_slot)
                if binding in bindings:raise ValueError('Actual PSU is bound more than once')
                bindings.add(binding)
            if node.kind=='fixed' and node.fixed_watts is None:raise ValueError('Fixed load needs fixed_watts')
        occupied=set();children={key:[] for key in nodes}
        for edge in self.edges:
            if edge.source==edge.target:raise ValueError('Self connections are invalid')
            for node_id,port_id,direction in ((edge.source,edge.source_port,'output'),(edge.target,edge.target_port,'input')):
                port=sockets.get(node_id,{}).get(port_id)
                if port is None or port.direction!=direction:raise ValueError('Invalid port or connection direction')
                key=(node_id,port_id)
                if key in occupied:raise ValueError('A socket can have only one cable')
                occupied.add(key)
            children[edge.source].append(edge.target)
        visiting=set();visited=set()
        def visit(key):
            if key in visiting:raise ValueError('Electrical cycles are invalid')
            if key in visited:return
            visiting.add(key)
            for child in children[key]:visit(child)
            visiting.remove(key);visited.add(key)
        for key in nodes:visit(key)
        return self
