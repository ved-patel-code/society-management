"""FastAPI app factory (docs/PF §10, docs/02 §3).

Registers the foundation module spec, mounts the platform routers, installs the
central error handler that renders ``DomainError`` → ``{code, message, details}``
(docs/03 §6), and exposes ``/health``. Feature module routers mount here later via
the registry with zero edits to existing modules.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.common.errors import DomainError
from app.core.config import settings
from app.modules.onboarding.router import router as onboarding_router
from app.modules.onboarding.spec import register_onboarding
from app.platform.auth.router import router as auth_router
from app.platform.bootstrap import register_foundation
from app.platform.roles.router import router as roles_router
from app.platform.societies.router import router as societies_router
from app.platform.users.me_router import router as me_router
from app.platform.users.router import router as users_router

logging.basicConfig(level=logging.INFO)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request validation failed.",
                "details": {"errors": exc.errors()},
            },
        )


def create_app() -> FastAPI:
    register_foundation()
    register_onboarding()

    app = FastAPI(
        title="Society Management API",
        version="0.1.0",
        description="Multi-tenant society management — Platform Foundation.",
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    _install_error_handlers(app)

    # Platform routers (feature agents fill these in; wiring stays here).
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(societies_router)
    app.include_router(users_router)
    app.include_router(roles_router)

    # Feature module routers.
    app.include_router(onboarding_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
