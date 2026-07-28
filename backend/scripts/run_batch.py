"""Command-line batch image analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.batch_analyzer import BatchAnalyzer


def main() -> int:
    """Parse arguments, run batch analysis, and print its summary."""
    parser = argparse.ArgumentParser(description="批量识别分子结构图片并导出结果")
    parser.add_argument("--input", required=True, help="包含 PNG/JPG/JPEG 的输入文件夹")
    parser.add_argument("--output", required=True, help="报告输出文件夹")
    parser.add_argument("--backend", choices=["demo", "molscribe", "decimer", "ensemble"], default="demo")
    args = parser.parse_args()
    try:
        result = BatchAnalyzer(args.backend, args.output).analyze_folder(args.input)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        print(f"CSV: {result['exports']['csv']}")
        print(f"JSON: {result['exports']['json']}")
        return 0
    except Exception as exc:
        print(f"批量处理失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
