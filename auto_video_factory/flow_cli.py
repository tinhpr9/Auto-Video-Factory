"""
Command-line interface for Google Flow Quality Scene Planner (V4.3).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from auto_video_factory.flow_planner import (
    DEFAULT_FLOW_MODE,
    FLOW_MODES,
    export_flow_pack,
    plan_flow_scenes,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flow_planner",
        description="Generate Google Flow Quality Scene Pack, copyable prompt set, and mobile checklist.",
    )
    parser.add_argument(
        "--topic",
        "-t",
        required=True,
        help="Story topic or theme",
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=45,
        choices=[45, 60, 90],
        help="Target video duration in seconds (default: 45)",
    )
    parser.add_argument(
        "--mode",
        "-m",
        default=DEFAULT_FLOW_MODE,
        choices=FLOW_MODES,
        help="Flow model routing mode (default: flow_balanced)",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=Path("flow_pack"),
        help="Output directory for generated pack files (default: flow_pack)",
    )

    args = parser.parse_args(argv)

    print(f"[FlowPlanner] Planning {args.duration}s scenes for: {args.topic!r}")
    print(f"[FlowPlanner] Mode: {args.mode}")

    pack = plan_flow_scenes(
        args.topic,
        duration_seconds=args.duration,
        flow_mode=args.mode,
    )

    paths = export_flow_pack(pack, args.out)

    print(f"[FlowPlanner] SUCCESS:")
    print(f"  - Total Scenes: {pack.total_scenes}")
    print(f"  - Estimated Credits: {pack.estimated_total_credits}")
    print(f"  - JSON Pack: {paths['json']}")
    print(f"  - Prompts:   {paths['prompts']}")
    print(f"  - Checklist: {paths['checklist']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
