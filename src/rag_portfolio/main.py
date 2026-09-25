import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from rag_portfolio import __version__
from rag_portfolio.adapters.vector import QdrantConnection
from rag_portfolio.api.routes import health, router
from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.database = Database(settings)
        app.state.vector = QdrantConnection(settings)
        try:
            yield
        finally:
            await app.state.vector.close()
            await app.state.database.close()

    app = FastAPI(
        title="RAG Portfolio",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        try:
            request.state.request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request.state.request_id = uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    def error(request: Request, status: int, code: str, **extra):
        return JSONResponse(
            {"error": {"code": code, **extra}, "request_id": str(request.state.request_id)},
            status_code=status,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        response = error(request, exc.status_code, str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        issues = [{"location": item["loc"], "type": item["type"]} for item in exc.errors()]
        return error(request, 422, "validation_error", issues=issues)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.warning(
            "database_unavailable request=%s type=%s", request.state.request_id, type(exc).__name__
        )
        return error(request, 503, "database_unavailable")

    app.include_router(health)
    app.include_router(router)
    return app
