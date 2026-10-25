FROM python:3.12-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.16
COPY pyproject.toml uv.lock ./
ARG UV_EXTRAS=""
RUN uv sync --locked --no-dev --no-install-project ${UV_EXTRAS}
COPY src ./src
RUN uv sync --locked --no-dev ${UV_EXTRAS}

FROM python:3.12-slim AS runtime
ARG BUILD_REVISION=development
ENV RAG_BUILD_REVISION=${BUILD_REVISION}
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
RUN mkdir -p /app/data /home/app/.cache/huggingface && chown -R app:app /app/data /home/app/.cache
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
CMD ["rag-api", "--host", "0.0.0.0", "--port", "8000"]
