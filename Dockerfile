FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Keep uv in the builder only. The version matches CI and is intentionally pinned.
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv lock --check \
    && uv export --locked --no-dev --no-emit-project --no-header \
        --format requirements.txt --output-file /runtime-requirements.txt \
    && python -m pip wheel --no-cache-dir --require-hashes --no-deps \
        --wheel-dir /wheels --requirement /runtime-requirements.txt

COPY src/ ./src/
RUN uv build --wheel --out-dir /wheels


FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="MCI Reliable Cluster Client" \
      org.opencontainers.image.description="One-shot CLI for reliable multi-node group operations" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/Hooman1996/mci-reliable-cluster-client" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12-slim-bookworm"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 10001 mci \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin mci \
    && mkdir /app \
    && chmod 0555 /app

RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-compile \
        --no-index --find-links=/wheels mci-reliable-cluster-client

WORKDIR /app
USER 10001:10001

ENTRYPOINT ["mci-cluster"]
CMD ["--help"]
