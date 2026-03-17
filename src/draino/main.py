from __future__ import annotations

import argparse

from .tui import run_tui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drain Kubernetes and OpenStack compute nodes for maintenance")
    parser.add_argument(
        "--config",
        help="Path to YAML config with cloud name, timeouts, drain args, and optional target mappings",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_tui(args.config)


if __name__ == "__main__":
    main()
