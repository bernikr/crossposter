# SPDX-FileCopyrightText: 2025 bernikr <hi@berni.kr>
#
# SPDX-License-Identifier: MIT

FROM ghcr.io/astral-sh/uv:trixie AS builder
SHELL ["sh", "-exc"]
WORKDIR /src

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PROJECT_ENVIRONMENT=/venv \
    UV_PREVIEW_FEATURES=frozen-lockfile

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project

COPY . /src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM gcr.io/distroless/cc-debian13:latest AS runtime

COPY --from=builder /python /python
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

ARG VERSION
ENV VERSION=${VERSION:-"unspecified"}
USER 65532:65532
# TODO: Add a healthcheck
CMD ["python", "-m", "main"]
