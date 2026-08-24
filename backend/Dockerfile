FROM python:3.12.14-alpine3.24@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements-bootstrap.lock requirements-build.lock requirements.lock ./
RUN python -m pip install --require-hashes -r requirements-bootstrap.lock && \
    python -m pip install --require-hashes -r requirements-build.lock && \
    python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.lock

COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels . && \
    python -m pip uninstall --yes pip

FROM python:3.12.14-alpine3.24@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/alpha/.local/bin:${PATH}"

RUN addgroup -S -g 65532 alpha && \
    adduser -S -D -u 65532 -G alpha -h /home/alpha alpha
COPY requirements-bootstrap.lock requirements.lock /tmp/
COPY --from=builder /wheels /wheels
RUN python -m pip install --require-hashes -r /tmp/requirements-bootstrap.lock && \
    python -m pip install \
      --no-index \
      --find-links=/wheels \
      --require-hashes \
      -r /tmp/requirements.lock && \
    python -m pip install --no-index --no-deps /wheels/alpha_ctf_backend-*.whl && \
    python -m pip uninstall --yes pip && \
    rm -rf /wheels /tmp/requirements-bootstrap.lock /tmp/requirements.lock

WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0555 /entrypoint.sh && chown -R alpha:alpha /app

USER 65532:65532
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]
ENTRYPOINT ["/entrypoint.sh"]
