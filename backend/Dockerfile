FROM python:3.11.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/app/data/models/cache
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 rag && useradd --uid 10001 --gid rag --create-home rag
# Docker Desktop here targets Linux x86_64. Install the lock's CPU-only wheel
# from the official PyTorch host before resolving the remainder: the index URL
# can redirect to a TLS endpoint that intermittently resets on Windows hosts.
COPY backend/requirements.lock ./requirements.lock
RUN python -m pip install --no-deps \
      https://download.pytorch.org/whl/cpu/torch-2.14.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl \
    && python -m pip install --index-url "${PIP_INDEX_URL}" --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock \
    && python -c "import torch; assert torch.version.cuda is None, 'CPU-only torch is required'"
COPY --chown=rag:rag backend/ ./
RUN mkdir -p /app/data/manuals /app/data/models /app/data/beat \
    && chown -R rag:rag /app/data
USER rag
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
