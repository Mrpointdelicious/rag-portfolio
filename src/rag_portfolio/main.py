import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from rag_portfolio import __version__
from rag_portfolio.adapters.embedding import EmbeddingAdapterError
from rag_portfolio.adapters.vector import QdrantConnection
from rag_portfolio.api.demo import router as demo_router
from rag_portfolio.api.knowledge import router as knowledge_router
from rag_portfolio.api.knowledge_search import router as knowledge_search_router
from rag_portfolio.api.routes import health, router
from rag_portfolio.api.scenes import router as scene_router
from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.services.documents import DocumentServiceError

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
        if request.url.path.startswith("/v1/experimental/demo/"):
            logger.info(
                "demo_http request=%s status=%s", request.state.request_id, response.status_code
            )
        return response

    def error(request: Request, status: int, code: str, **extra):
        return JSONResponse(
            {
                "error": {"code": code, "retryable": status in (429, 502, 503, 504), **extra},
                "request_id": str(request.state.request_id),
            },
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

    @app.exception_handler(DocumentServiceError)
    async def document_error(request: Request, exc: DocumentServiceError):
        return error(request, exc.status_code, exc.code)

    @app.exception_handler(EmbeddingAdapterError)
    @app.exception_handler(ResponseHandlingException)
    @app.exception_handler(UnexpectedResponse)
    async def provider_error(request: Request, exc: Exception):
        logger.warning(
            "provider_unavailable request=%s type=%s", request.state.request_id, type(exc).__name__
        )
        return error(request, 503, "provider_unavailable")

    app.include_router(health)
    app.include_router(router)
    app.include_router(demo_router)
    app.include_router(knowledge_router)
    app.include_router(knowledge_search_router)
    app.include_router(scene_router)
    return app
