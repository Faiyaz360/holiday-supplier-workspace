FROM ghcr.io/astral-sh/uv:0.12.14 AS uv
FROM python:3.13-slim-bookworm
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY reconciliation ./reconciliation
COPY reconciler ./reconciler
COPY data ./data
RUN useradd --create-home demo && mkdir outputs && chown demo:demo outputs
USER demo
EXPOSE 8080
ENV PORT=8080
CMD ["python", "-m", "reconciliation", "serve-hosted", "--host", "0.0.0.0"]
