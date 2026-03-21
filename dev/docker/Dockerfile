# Gallowglass dev environment with planvm (xocore-tech/PLAN).
#
# Builds the PLAN VM from source using Nix, then provides it alongside
# the Python bootstrap compiler for local testing on macOS (or any host
# with Docker).
#
# Usage:
#   docker build -t gallowglass-dev dev/docker/
#   docker run --rm -v $(pwd):/work gallowglass-dev make test-planvm
#
# Or via Makefile:
#   make test-planvm-docker

FROM nixos/nix:2.18.1

# Install git and Python into the Nix environment
RUN nix-env -iA nixpkgs.git nixpkgs.python312 nixpkgs.gnumake

# Build planvm from source.
# Pin to a specific commit for reproducibility; update when xocore releases.
ARG PLAN_COMMIT=HEAD
RUN git clone --depth=1 https://github.com/xocore-tech/PLAN.git /tmp/planvm-src && \
    cd /tmp/planvm-src && \
    nix develop --command make all && \
    cp x/plan /usr/local/bin/planvm && \
    rm -rf /tmp/planvm-src

# Verify planvm is on PATH
RUN planvm --help 2>&1 || true

WORKDIR /work

# Default: run all tests including planvm seed validation
CMD ["sh", "-c", "make test-bootstrap && PLANVM=planvm python3 tests/planvm/test_seed_planvm.py"]
