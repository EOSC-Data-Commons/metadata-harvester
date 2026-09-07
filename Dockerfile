FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

COPY ./pyproject.toml ./uv.lock /app/

WORKDIR /app

ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project

COPY harvester ./harvester
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "-m", "harvester"]
