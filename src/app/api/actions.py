"""Action discovery endpoints (build plan Phase 2.6, section 21).

The frontend populates its Action selector exclusively from this endpoint. No
Action list is ever hardcoded in the browser, which is what allows a new Action
to appear in the UI without a single frontend change (build plan section 3.2).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.actions import registry
from app.models.schemas import ActionListResponse

router = APIRouter(prefix="/api", tags=["actions"])


@router.get("/actions", response_model=ActionListResponse)
def get_actions() -> ActionListResponse:
    """Return the definition of every registered Action."""
    return ActionListResponse(
        actions=tuple(action.definition() for action in registry.list_actions())
    )