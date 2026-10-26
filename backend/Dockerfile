FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
WORKDIR /workspace/AgentChaos/backend
RUN pip install --no-cache-dir uv==0.12.21
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY knowledge /workspace/AgentChaos/knowledge
COPY docs/evidence/T04/model-probe.json /workspace/AgentChaos/docs/evidence/T04/model-probe.json
RUN uv sync --locked --no-dev
RUN mkdir -p /workspace/AgentChaos/.local /workspace/AgentChaos/.cache/jieba && chown -R 10001:10001 /workspace/AgentChaos/.local /workspace/AgentChaos/.cache
ENV PATH="/workspace/AgentChaos/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
