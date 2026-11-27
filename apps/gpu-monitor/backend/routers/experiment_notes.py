"""Read current experiment notes and update their administrative setting."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

try:
    from ..experiment_notes import ExperimentNotes, ExperimentNotesState
    from .servers import _check_admin
except ImportError:  # pragma: no cover - direct execution fallback
    from experiment_notes import ExperimentNotes, ExperimentNotesState
    from routers.servers import _check_admin

router = APIRouter(prefix="/experiment-notes", tags=["experiment-notes"])


class EditExperimentNotesSettings(BaseModel):
    enabled: StrictBool
    admin_password: str = Field(max_length=1024)


@router.get("", response_model=ExperimentNotesState)
async def get_experiment_notes(request: Request) -> ExperimentNotesState:
    return request.app.state.experiment_notes.snapshot()


@router.put("/settings", response_model=ExperimentNotesState)
async def set_experiment_notes(request: Request, body: EditExperimentNotesSettings) -> ExperimentNotesState:
    _check_admin(body.admin_password)
    notes: ExperimentNotes = request.app.state.experiment_notes
    if body.enabled and not notes.configured:
        raise HTTPException(status_code=409, detail="Experiment notes API is not configured")
    await notes.set_enabled(body.enabled)
    return notes.snapshot()
