"""
Eval script for Google Flow Mode Routing & Model Override Precedence (Promptfoo compatible).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_video_factory.flow_provider.models import (
    FlowModel,
    FLOW_MODE_TO_MODEL,
    FLOW_MODEL_MAP,
    resolve_flow_model,
)
from auto_video_factory.flow_provider.visual_provider import FlowVisualProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.provider import MockFlowProvider


def main() -> int:
    context = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    vars_ = context.get("vars", {})
    flow_mode = vars_.get("flow_mode", "flow_balanced")
    flow_model_arg = vars_.get("flow_model")

    explicit_model = resolve_flow_model(flow_model_arg) if flow_model_arg else None

    tmp_dir = Path(tempfile.gettempdir())
    mock_prov = MockFlowProvider(initial_credits=500)
    controller = FlowController(
        provider=mock_prov,
        storage_path=tmp_dir / "flow_routing_eval_jobs.json",
        output_dir=tmp_dir / "flow_routing_eval_scenes",
    )
    vp = FlowVisualProvider(
        controller=controller,
        model=explicit_model,
        flow_mode=flow_mode,
    )
    result = {
        "flow_mode": vp.flow_mode,
        "explicit_model": vp.explicit_model.value if vp.explicit_model else None,
        "resolved_model": vp.model.value,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
