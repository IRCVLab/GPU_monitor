"""Read-only, timestamped hardware and measured power history."""
from pathlib import Path
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, HTTPException

try:
    from ..hardware import read_hardware_snapshots
except ImportError:  # direct execution from backend/
    from hardware import read_hardware_snapshots

router = APIRouter(prefix='/system', tags=['system'])
_SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / 'data' / 'hardware-snapshots.json'


@router.get('/hardware')
async def get_hardware_snapshots(request: Request):
    monitor = request.app.state.hardware_monitor
    if monitor.current is not None:
        return {'mode': 'live', 'servers': monitor.current['servers'], 'interval': monitor.interval}
    return {'mode': 'snapshot', 'servers': read_hardware_snapshots(_SNAPSHOT_PATH), 'interval': monitor.interval}


@router.get('/power-history')
async def get_power_history(request: Request, hours: int = Query(1, ge=1, le=168),
                            network: Literal['internal', 'external', 'all'] = 'internal',
                            source: Literal['dcmi', 'ac_input'] = 'dcmi'):
    return await request.app.state.hardware_monitor.power_history(hours, network, source=source)


@router.get('/power-rollups')
async def get_power_rollups(request: Request, resolution: Literal['minute','hour']='hour',
                           days: int=Query(365,ge=1,le=3650),
                           network: Literal['internal','external','all']='internal',
                           source: Literal['dcmi','ac_input']='dcmi', after: datetime | None=None):
    if resolution == 'minute' and days > 365:
        raise HTTPException(status_code=422,detail='minute rollups support at most 365 days')
    if after is not None and after.tzinfo is None:
        raise HTTPException(status_code=422,detail='after must include UTC offset')
    return await request.app.state.hardware_monitor.power_rollups(resolution,days,network,source,after=after)


@router.get('/temperature-history')
async def get_temperature_history(request: Request,hours: int=Query(1,ge=1,le=168),
                                  network: Literal['internal','external','all']='internal'):
    return await request.app.state.hardware_monitor.temperature_history(hours,network)


@router.get('/server-history')
async def get_server_history(request: Request,hours: int=Query(1,ge=1,le=168),
                             network: Literal['internal','external','all']='internal'):
    return await request.app.state.hardware_monitor.server_history(hours,network)
