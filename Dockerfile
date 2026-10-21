# FluxScope, ready to run: Slither, Foundry (solc) and the bundled projects. No build on the host.
#
#   docker run --rm -p 8765:8765 ghcr.io/pypy233/fluxscope
#       -> http://localhost:8765 : the viewer with the "Analyze project" panel (bundled projects)
#   docker run --rm -v "$PWD/out:/out" ghcr.io/pypy233/fluxscope \
#       fluxscope analyze corpus/kaia-yield --html /out/kaia-yield.html
#
# Built for linux/amd64 and linux/arm64 (.github/workflows/docker.yml).
FROM python:3.12-slim

LABEL org.opencontainers.image.title="FluxScope" \
      org.opencontainers.image.description="DeFi asset-flow analysis of Solidity projects with a code-linked offline viewer" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Foundry: forge drives the Solidity compiler for crytic-compile / Slither. Same versions as the
# evaluation in docs/evaluation.md.
ARG FOUNDRY_VERSION=v1.7.1
RUN curl -sSfL https://foundry.paradigm.xyz | bash \
    && /root/.foundry/bin/foundryup --install "${FOUNDRY_VERSION}"
ENV PATH="/root/.foundry/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /fluxscope
# dependencies first (from pyproject.toml), so that a code change does not reinstall them
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['server']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt "slither-analyzer==0.11.6" "crytic-compile==0.4.2"
COPY README.md LICENSE ./
COPY fluxscope ./fluxscope
RUN pip install --no-cache-dir --no-deps .
COPY corpus ./corpus
COPY examples ./examples
COPY schema ./schema
COPY eval ./eval
COPY scripts ./scripts

# Compile every bundled project once: puts the needed solc versions into the image (so it works
# offline) and fails the build if the toolchain does not work.
RUN for p in beefy-erc4626 kaia-yield metamorpho fixtures/receiver-vault fixtures/oz-erc4626; do \
        fluxscope analyze "corpus/$p" --quiet --json "/tmp/$(basename "$p").json" || exit 1; \
    done && rm -f /tmp/*.json

EXPOSE 8765
CMD ["fluxscope", "serve", "--host", "0.0.0.0", "--port", "8765", "--projects", "corpus"]
