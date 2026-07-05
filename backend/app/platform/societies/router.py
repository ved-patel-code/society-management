"""Super-admin society + module-allocation routes (docs/PF §6/§10).

Endpoints: POST/GET/PATCH /admin/societies, PUT /admin/societies/{id}/modules.
All gate on ``require_super_admin`` (core/deps.py). The router stays thin — parse
the request, call ``SocietyService``, shape the response (docs/03 §2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.common.pagination import Page, PageParams
from app.core.db import get_session
from app.core.deps import AuthContext, require_super_admin
from app.platform.societies.schemas import (
    ModuleAllocationRequest,
    SocietyCreate,
    SocietyModuleOut,
    SocietyOut,
    SocietyUpdate,
)
from app.platform.societies.service import SocietyService

router = APIRouter(prefix="/admin", tags=["admin:societies"])


@router.post(
    "/societies",
    response_model=SocietyOut,
    status_code=status.HTTP_201_CREATED,
)
def create_society(
    body: SocietyCreate,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> SocietyOut:
    """Create a society (type NULL, status onboarding) + instantiate its roles."""
    society = SocietyService(session).create_society(
        body, actor_user_id=auth.user_id
    )
    return SocietyOut.model_validate(society)


@router.get("/societies", response_model=Page[SocietyOut])
def list_societies(
    params: PageParams = Depends(),
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> Page[SocietyOut]:
    """Paginated list of societies (newest first)."""
    items, total = SocietyService(session).list_societies(
        limit=params.limit, offset=params.offset
    )
    return Page[SocietyOut](
        items=[SocietyOut.model_validate(s) for s in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/societies/{society_id}", response_model=SocietyOut)
def get_society(
    society_id: int,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> SocietyOut:
    """Fetch a single society by id."""
    society = SocietyService(session).get_society(society_id)
    return SocietyOut.model_validate(society)


@router.patch("/societies/{society_id}", response_model=SocietyOut)
def update_society(
    society_id: int,
    body: SocietyUpdate,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> SocietyOut:
    """Patch mutable society config (audits a before/after diff)."""
    society = SocietyService(session).update_society(
        society_id, body, actor_user_id=auth.user_id
    )
    return SocietyOut.model_validate(society)


@router.put(
    "/societies/{society_id}/modules",
    response_model=list[SocietyModuleOut],
)
def set_society_modules(
    society_id: int,
    body: ModuleAllocationRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> list[SocietyModuleOut]:
    """Allocate/toggle modules for a society (enforces depends_on)."""
    modules = SocietyService(session).set_modules(
        society_id, body.modules, actor_user_id=auth.user_id
    )
    return [SocietyModuleOut.model_validate(m) for m in modules]
