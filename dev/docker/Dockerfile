# Gallowglass dev environment with planvm (xocore-tech/PLAN).
#
# Pinned to linux/amd64: the PLAN VM flake only supports x86_64-linux,
# matching the CI environment. On Apple Silicon, Docker Desktop handles
# amd64 emulation via Rosetta 2 (slower but correct).
#
# Usage:
#   docker build -t gallowglass-dev dev/docker/
#   docker run --rm -v $(pwd):/work gallowglass-dev make test-planvm
#
# Or via Makefile:
#   make test-planvm-docker

FROM --platform=linux/amd64 nixos/nix:2.18.1

# Enable nix-command and flakes (required for `nix develop`).
RUN mkdir -p /etc/nix && \
    echo 'experimental-features = nix-command flakes' >> /etc/nix/nix.conf

# Install git and Python into the Nix environment.
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
