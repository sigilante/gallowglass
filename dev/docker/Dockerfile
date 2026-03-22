# Gallowglass dev environment with planvm (xocore-tech/PLAN).
#
# planvm is a C + x86_64 assembly project.  The generated .s and .incc.c
# files are already committed to the PLAN repo, so the only build requirement
# is gcc + binutils — no Nix needed.
#
# Pinned to linux/amd64: the PLAN VM is x86_64 assembly.
# On Apple Silicon, Docker Desktop handles amd64 emulation via Rosetta 2.
#
# Usage:
#   make docker-build          # build image (first time, ~2 min)
#   make test-planvm-docker    # run planvm seed validation

FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# gcc + binutils to compile planvm; python3 + git + make for tests/clone.
RUN apt-get update && apt-get install -y \
        gcc binutils git python3 make ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Clone and build planvm (C + x86_64 assembly, no Nix required).
# The generated planvm-amd64.s / planvm-amd64.incc.c are already committed.
# Ubuntu 22.04 GCC defaults to PIE; the assembly uses R_X86_64_32S (32-bit
# absolute) relocations incompatible with PIE, so we override with -fno-pie.
RUN git clone --depth=1 https://github.com/xocore-tech/PLAN.git /tmp/planvm-src && \
    mkdir -p /tmp/planvm-src/x && \
    make -C /tmp/planvm-src x/plan \
      "CFLAGS=-std=gnu99 -O0 -g -fno-stack-protector -z noexecstack -msse4.2 -Wall -Werror -fdiagnostics-color=always -fno-pie -no-pie" && \
    cp /tmp/planvm-src/x/plan /usr/local/bin/planvm && \
    rm -rf /tmp/planvm-src

# Verify planvm is on PATH.
RUN planvm --help 2>&1 || true

WORKDIR /work

# Default: run planvm seed validation against the mounted repo.
CMD ["sh", "-c", "PLANVM=planvm python3 tests/planvm/test_seed_planvm.py"]
