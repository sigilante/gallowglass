"""Console-scripts entry point: `gallowglass-kernel install`."""
import sys
from bootstrap.jupyter_kernel import _cli_main

def main():
    _cli_main(sys.argv)
