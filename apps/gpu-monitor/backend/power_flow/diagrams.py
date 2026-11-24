"""Read and write saved Power Flow diagrams and their immutable revisions."""

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from .. import telemetry_time
    from ..telemetry_time import iso, utc_naive
    from ..models import PowerFlowDiagram, PowerFlowRevision
except ImportError:  # pragma: no cover - direct execution fallback
    import telemetry_time
    from telemetry_time import iso, utc_naive
    from models import PowerFlowDiagram, PowerFlowRevision

from .schema import FlowGraph


def _summary(diagram: PowerFlowDiagram) -> dict:
    return {
        'id': diagram.id,
        'name': diagram.name,
        'version': diagram.version,
        'is_scenario': bool(diagram.is_scenario),
        'created_at': iso(diagram.created_at),
        'updated_at': iso(diagram.updated_at),
    }


def _document(diagram: PowerFlowDiagram, revision: PowerFlowRevision) -> dict:
    return {
        **_summary(diagram),
        'name': revision.name,
        'version': revision.version,
        'graph': revision.graph,
        'effective_from': iso(revision.effective_from),
    }


async def list_diagrams(db: AsyncSession) -> list[dict]:
    query = select(PowerFlowDiagram).order_by(PowerFlowDiagram.updated_at.desc(), PowerFlowDiagram.id)
    return [_summary(row) for row in await db.scalars(query)]


async def get_diagram(db: AsyncSession, diagram_id: str) -> PowerFlowDiagram:
    diagram = await db.get(PowerFlowDiagram, diagram_id)
    if diagram is None:
        raise HTTPException(404, 'Diagram not found')
    return diagram


async def find_revision(
    db: AsyncSession, diagram: PowerFlowDiagram, *, at: datetime | None = None,
) -> PowerFlowRevision | None:
    query = select(PowerFlowRevision).where(PowerFlowRevision.diagram_id == diagram.id)
    if at is None or diagram.is_scenario:
        query = query.where(PowerFlowRevision.version == diagram.version)
    else:
        query = (
            query.where(PowerFlowRevision.effective_from <= utc_naive(at))
            .order_by(PowerFlowRevision.effective_from.desc(), PowerFlowRevision.version.desc())
            .limit(1)
        )
    return await db.scalar(query)


async def current_document(db: AsyncSession, diagram_id: str) -> dict:
    diagram = await get_diagram(db, diagram_id)
    return _document(diagram, await find_revision(db, diagram))


async def create_diagram(
    db: AsyncSession, name: str, graph: FlowGraph, is_scenario: bool,
) -> dict:
    timestamp = utc_naive(telemetry_time.now_utc())
    diagram = PowerFlowDiagram(
        id=str(uuid4()), name=name, version=1, is_scenario=int(is_scenario),
        created_at=timestamp, updated_at=timestamp,
    )
    revision = PowerFlowRevision(
        diagram_id=diagram.id, version=1, name=name, graph=graph.model_dump(),
        effective_from=timestamp,
    )
    db.add(diagram)
    await db.flush()
    db.add(revision)
    await db.commit()
    return _document(diagram, revision)


async def save_diagram(
    db: AsyncSession, diagram_id: str, base_version: int, name: str, graph: FlowGraph,
) -> dict:
    await get_diagram(db, diagram_id)
    timestamp = utc_naive(telemetry_time.now_utc())
    version = base_version + 1
    try:
        result = await db.execute(
            update(PowerFlowDiagram)
            .where(PowerFlowDiagram.id == diagram_id, PowerFlowDiagram.version == base_version)
            .values(version=version, name=name, updated_at=timestamp)
        )
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, 'Diagram changed; reload before saving')
        db.add(PowerFlowRevision(
            diagram_id=diagram_id, version=version, name=name, graph=graph.model_dump(),
            effective_from=timestamp,
        ))
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(409, 'Diagram changed; reload before saving') from error
    return await current_document(db, diagram_id)
