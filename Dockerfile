# syntax=docker/dockerfile:1
#
# Images from one file, selected by compose.yaml with `target:`.
#
#   trustvian-build   builds trustvian and trustvian-local from a Trustvian
#                     checkout (pinned with TRUSTVIAN_REF)
#   collector         the official, signed trustvian-collector release image
#                     (pinned by digest with TRUSTVIAN_COLLECTOR_IMAGE); only
#                     its binary is copied out
#   control-plane     trustvian-local behind a tiny TCP relay (see
#                     compose/control-plane.sh for why the relay exists)
#   agent             Python + the OpenAI SDK + the instrumentation harness, plus the
#                     trustvian CLI and the collector that `trustvian dev`
#                     supervises
#
# Nothing here is pushed anywhere; everything except the collector is built
# locally by `docker compose build`.

# The released Collector: ghcr.io/trustvian/trustvian-collector, v0.12.0,
# pinned by the digest of its multi-arch index (Docker picks the amd64 or
# arm64 image from it). The tag is informational; the digest is what is
# pulled. Verify it with cosign as in Trustvian's docs/supply-chain.md.
# Declared before the first FROM so a FROM can use it.
ARG TRUSTVIAN_COLLECTOR_IMAGE=ghcr.io/trustvian/trustvian-collector:v0.12.0@sha256:65bb1b61d35b57b0a1efb9d237f71d0cc02e7b41d1cd30d4a58023c7d0a022f0

# ---------------------------------------------------------------------
# Stage 0: the Collector, from the release image (distroless, static).
# ---------------------------------------------------------------------
FROM ${TRUSTVIAN_COLLECTOR_IMAGE} AS collector

# ---------------------------------------------------------------------
# Stage 1: the CLI and the control plane, from source.
#
# Neither is published as an image (the release image holds only the
# Collector), so both are built here. Mirrors scripts/release-build.sh:
# Go 1.27 (what go.mod declares), CGO_ENABLED=0 (every dependency is pure
# Go, so the binaries are static), GOWORK=off for the nested platform module
# (it must resolve dependencies the way a consumer does, not the way a
# developer's workspace does), -trimpath so no build paths are embedded.
# TRUSTVIAN_REF should name the same release as the collector image.
# ---------------------------------------------------------------------
FROM golang:1.27-alpine AS trustvian-build

RUN apk add --no-cache git

ARG TRUSTVIAN_REPO=https://github.com/trustvian/trustvian.git
# A branch, a tag or a full commit SHA. Pin a SHA for a reproducible image.
ARG TRUSTVIAN_REF=v0.12.0

WORKDIR /src
# fetch-by-ref rather than clone --branch, so a commit SHA works as well as
# a branch or tag.
RUN git init -q . \
 && git remote add origin "$TRUSTVIAN_REPO" \
 && git fetch -q --depth 1 origin "$TRUSTVIAN_REF" \
 && git checkout -q FETCH_HEAD

RUN go mod download \
 && cd platform && GOWORK=off go mod download

RUN CGO_ENABLED=0 go build -trimpath -o /out/trustvian ./cmd/trustvian \
 && cd platform && CGO_ENABLED=0 GOWORK=off go build -trimpath -o /out/trustvian-local ./cmd/trustvian-local

# ---------------------------------------------------------------------
# Stage 2: the control plane.
#
# trustvian-local binds loopback only, by design (it is unauthenticated).
# Inside a container that loopback is reachable by nobody — not by the
# other services and not by the browser on the host — so a socat relay
# forwards 0.0.0.0:8080 in the container to the loopback listener.
# compose.yaml publishes that port on the host's 127.0.0.1 only.
# ---------------------------------------------------------------------
FROM alpine:3.22 AS control-plane

RUN apk add --no-cache socat

COPY --from=trustvian-build /out/trustvian-local /usr/local/bin/trustvian-local
COPY compose/control-plane.sh /usr/local/bin/control-plane
RUN chmod +x /usr/local/bin/control-plane

VOLUME ["/var/lib/trustvian"]
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/control-plane"]

# ---------------------------------------------------------------------
# Stage 3: the agent runner.
#
# `trustvian dev` runs here, attached to the control-plane service with
# --api-url. It supervises trustvian-collector (copied out of the release
# image, beside the CLI, which is one of the places dev looks) and the
# Python workload. The workload's code is bind-mounted at runtime by
# compose.yaml, so editing agent.py or tools.py needs no rebuild; only requirements.txt changes do.
# ---------------------------------------------------------------------
FROM python:3.12-slim AS agent

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl procps \
 && rm -rf /var/lib/apt/lists/*

COPY --from=trustvian-build /out/trustvian /out/trustvian-local /usr/local/bin/
COPY --from=collector /usr/local/bin/trustvian-collector /usr/local/bin/trustvian-collector

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# dev keeps its state under $HOME/.trustvian; compose.yaml mounts a volume
# there so the learned baseline survives between `docker compose run`s.
ENV HOME=/root
# Invoked through bash, so the script needs no executable bit — compose.yaml
# bind-mounts this directory over /app, and a checkout on the host may not
# carry one.
ENTRYPOINT ["bash", "/app/compose/agent-entrypoint.sh"]
CMD ["help"]
