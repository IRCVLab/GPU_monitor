"""Power Flow read-only observations and administrator-authored topology."""
from datetime import datetime, timedelta
from typing import Annotated
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
try:
    from ..config import get_settings
    from ..database import get_db
    from ..models import PowerFlowDiagram
    from ..power_flow.schema import Contract, FlowGraph
    from ..power_flow import service
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import get_db
    from models import PowerFlowDiagram
    from power_flow.schema import Contract, FlowGraph
    from power_flow import service

router=APIRouter(prefix='/power-flow',tags=['power-flow'])
Hours=Annotated[int,Field(ge=1,le=720)]


def check_at(value):
    if value is not None and (value.tzinfo is None or value.utcoffset()!=timedelta(0)):
        raise ValueError('at must be an explicit UTC datetime (Z or +00:00)')
    return value


class CreateRequest(Contract):
    name: str = Field(min_length=1,max_length=120)
    graph: FlowGraph = Field(default_factory=FlowGraph)
    is_scenario: bool = False
    admin_password: SecretStr

    @field_validator('name')
    @classmethod
    def name_nonempty(cls,value):
        if not value.strip():raise ValueError('Name is required')
        return value.strip()


class SaveRequest(Contract):
    name: str = Field(min_length=1,max_length=120)
    base_version: Annotated[int,Field(ge=1)]
    graph: FlowGraph
    admin_password: SecretStr
    _name=field_validator('name')(CreateRequest.name_nonempty.__func__)


class CloneRequest(Contract):
    name: str = Field(min_length=1,max_length=120)
    admin_password: SecretStr
    _name=field_validator('name')(CreateRequest.name_nonempty.__func__)


class EvaluateRequest(Contract):
    graph: FlowGraph
    hours: Hours = 24
    at: datetime | None = None
    include_peaks: bool = True
    _at=field_validator('at')(check_at)


def check_admin(password):
    if not secrets.compare_digest(password.get_secret_value().encode('utf-8'),get_settings().admin_password.encode('utf-8')):
        raise HTTPException(401,'Invalid admin password')


@router.get('/catalog')
async def get_catalog(db:AsyncSession=Depends(get_db)):
    return await service.catalog(db,service.now_utc(),get_settings().hardware_collect_interval)


@router.get('/diagrams')
async def list_diagrams(db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(PowerFlowDiagram).order_by(PowerFlowDiagram.updated_at.desc(),PowerFlowDiagram.id))).scalars().all()
    return [service.summary(row) for row in rows]


@router.post('/diagrams',status_code=201)
async def create_diagram(body:CreateRequest,db:AsyncSession=Depends(get_db)):
    check_admin(body.admin_password)
    return await service.create_diagram(db,body.name,body.graph,body.is_scenario)


@router.post('/evaluate')
async def evaluate_graph(body:EvaluateRequest,db:AsyncSession=Depends(get_db)):
    return await service.evaluate_state(db,body.graph,body.at or service.now_utc(),body.hours,interval_seconds=get_settings().hardware_collect_interval,include_peaks=body.include_peaks)


@router.get('/diagrams/{diagram_id}')
async def get_diagram(diagram_id:str,db:AsyncSession=Depends(get_db)):
    return await service.current_document(db,diagram_id)


@router.put('/diagrams/{diagram_id}')
async def save_diagram(diagram_id:str,body:SaveRequest,db:AsyncSession=Depends(get_db)):
    check_admin(body.admin_password)
    return await service.save_diagram(db,diagram_id,body.base_version,body.name,body.graph)


@router.post('/diagrams/{diagram_id}/clone',status_code=201)
async def clone_diagram(diagram_id:str,body:CloneRequest,db:AsyncSession=Depends(get_db)):
    check_admin(body.admin_password)
    source=await service.current_document(db,diagram_id)
    return await service.create_diagram(db,body.name,FlowGraph.model_validate(source['graph']),True)


@router.get('/diagrams/{diagram_id}/state')
async def get_state(diagram_id:str,hours:Annotated[int,Query(ge=1,le=720)]=24,at:datetime | None=None,include_peaks:bool=True,db:AsyncSession=Depends(get_db)):
    try:check_at(at)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return await service.diagram_state(db,diagram_id,at or service.now_utc(),hours,get_settings().hardware_collect_interval,include_peaks=include_peaks)
