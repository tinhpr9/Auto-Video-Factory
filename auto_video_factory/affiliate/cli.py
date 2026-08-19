"""
CLI commands for V5 Auto Affiliate Factory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .models import AffiliatePack
from .planner import plan_affiliate_pack
from .validation import validate_product_input


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_video_factory.affiliate",
        description="Auto Affiliate Factory (V5) CLI — generate 3 high-converting affiliate video variants.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-command to execute")

    plan_parser = subparsers.add_parser("plan", help="Generate 3 creative affiliate variants from product.json")
    plan_parser.add_argument("--product", "-p", required=True, help="Path to input product JSON file")
    plan_parser.add_argument("--duration", "-d", type=int, default=20, help="Target video duration in seconds (15, 20, 30)")
    plan_parser.add_argument("--output-dir", "-o", default="./affiliate_pack", help="Directory to save generated affiliate pack")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "plan":
        prod_path = Path(args.product)
        if not prod_path.exists():
            print(f"Error: Product file not found: {prod_path}", file=sys.stderr)
            return 1

        try:
            raw_data = json.loads(prod_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error parsing product JSON {prod_path}: {exc}", file=sys.stderr)
            return 1

        try:
            product = validate_product_input(raw_data)
            pack = plan_affiliate_pack(product, duration_seconds=args.duration)
        except Exception as exc:
            print(f"Error planning affiliate pack: {exc}", file=sys.stderr)
            return 1

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        pack_file = out_dir / "affiliate_pack.json"
        pack_file.write_text(json.dumps(pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        # Also write individual script text files for convenience
        for v in pack.variants:
            script_file = out_dir / f"script_{v.variant_type.value}.txt"
            script_file.write_text(v.voice_script, encoding="utf-8")

        print(f"Successfully generated 3 affiliate variants for '{product.title}' into: {out_dir}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
