# Build context is the REPOSITORY ROOT, not this directory:
#   docker build -f sandbox-service/Dockerfile .
# The uv workspace resolves crczp-* libraries from libs/, so their sources must
# be inside the build context.

FROM python:3.12-slim AS builder
# Must match the app stage's WORKDIR: console-script shebangs in .venv/bin are
# absolute, so the venv is only valid at the path it was created at.
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Pinned in step with .github/workflows/github-actions.yml and .pre-commit-config.yaml,
# so one uv version writes and reads uv.lock everywhere. This replaces an apt gcc layer
# plus an unpinned `pip install uv`: gcc is not needed, because every compiled dependency
# in the locked set ships a cp312/abi3 manylinux wheel (psycopg2-binary, cryptography,
# hiredis, ruamel.yaml.clib, bcrypt, pynacl), and the one sdist-only package, coreschema,
# is pure Python.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

# Workspace manifests and every member's source, so uv can resolve and build
# the crczp-* path dependencies.
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY sandbox-service sandbox-service

# --frozen           build strictly from uv.lock; fail rather than re-resolve
# --package          only sandbox-service and its dependencies, not every member
# --no-editable      bake real copies of the crczp-* libs into site-packages, so
#                    the venv stays valid once copied to /app in the next stage
#                    (editable installs would point back at build-time paths)
# --no-install-project  sandbox-service's own code is shipped as source below
# --no-default-groups   `dev` is uv's default group, so without this the image
#                    ships the ty type stubs (django-stubs, types-*): 140 packages
#                    instead of 134. Also what makes the tox audit env, which
#                    exports with the same flag, cover exactly this set.
RUN uv sync --frozen --package crczp-sandbox-service --no-editable --no-install-project \
    --no-default-groups

FROM python:3.12-slim AS app
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends gnupg curl unzip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/log/supervisor

## Install OpenTofu from script
RUN curl --proto '=https' --tlsv1.2 -fsSL https://get.opentofu.org/install-opentofu.sh -o install-opentofu.sh && \
chmod +x install-opentofu.sh && \
./install-opentofu.sh --install-method standalone && \
rm -rf install-opentofu.sh /tmp/*

# Setup provider mirror
WORKDIR /opt/tofu
COPY sandbox-service/tofu/openstack.tf .
COPY sandbox-service/tofu/tofu.rc /opt/tofu/config/tofu.rc
RUN tofu init && tofu providers mirror /opt/tofu/provider_mirror
ENV TF_CLI_CONFIG_FILE=/opt/tofu/config/tofu.rc

WORKDIR /app

COPY sandbox-service/bin bin
COPY sandbox-service/crczp crczp
COPY sandbox-service/config.yml-template ./config.yml
COPY sandbox-service/manage.py ./
COPY --from=builder /app/.venv ./.venv

# static files must be served from proxy server, expose them via volume bind
RUN python manage.py collectstatic --no-input -v 2

EXPOSE 8000
ENTRYPOINT ["/app/bin/run-sandbox-service.sh"]
