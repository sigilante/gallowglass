"""Entry point for `python -m gallowglass_kernel -f {connection_file}`.

Jupyter invokes this when a kernel process is started. All arguments
are forwarded to ipykernel; the -f flag carries the connection file path.
"""
import sys
from bootstrap.jupyter_kernel import _kernel_main

_kernel_main()
