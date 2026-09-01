# A container with MUNGE, for the checks that need real credentials. The host may not
# have libmunge, and nothing is installed on it: controller and CLI both run in here.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl munge libmunge-dev rsync openssh-client sudo \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

# Keep every write out of the mounted workspace: the venvs and the uv cache live in
# container-local paths backed by named volumes.
ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
