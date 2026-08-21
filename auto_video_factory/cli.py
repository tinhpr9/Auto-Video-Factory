from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .media import CardImageProvider, EspeakTTS, FFmpegRenderer
from .openai_providers import (
    OpenAIHTTPClient,
    OpenAIImageProvider,
    OpenAIStoryPlanner,
    OpenAITTS,
    ProviderError,
)
from .pipeline import VideoFactory
from .presets import DURATION_TO_SCENES, STYLE_OPTIONS
from .story import TemplateStoryPlanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-video-factory",
        description="Create a vertical narrated storytelling video from one topic.",
    )
    parser.add_argument("--topic", required=True, help="Story idea/topic")
    parser.add_argument("--output", default="output/latest", help="Job output directory")
    scene_group = parser.add_mutually_exclusive_group()
    scene_group.add_argument("--scenes", type=int, help="Number of scenes (2-12); default 8")
    scene_group.add_argument(
        "--duration-seconds",
        type=int,
        choices=tuple(DURATION_TO_SCENES),
        help="Target short-video duration preset; maps to a tested scene count",
    )
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--provider",
        choices=("offline", "openai", "flow"),
        default="offline",
        help="offline is free/local; openai enables AI script, image and TTS; flow uses Google Flow video provider",
    )

    # Offline provider controls.
    parser.add_argument("--voice", default="vi", help="offline eSpeak voice/language")
    parser.add_argument("--speed", type=int, default=165, help="offline eSpeak words per minute")

    # Flow provider controls.
    parser.add_argument(
        "--flow-mode",
        choices=("flow_balanced", "flow_economy", "flow_quality"),
        default="flow_balanced",
        help="Flow model routing mode",
    )
    parser.add_argument(
        "--flow-model",
        choices=("veo-3.1-fast", "veo-3.1-quality", "veo-2.1-fast"),
        default=None,
        help="Explicit Flow model override",
    )
    parser.add_argument(
        "--flow-storage-path",
        default=None,
        help="Path to flow jobs persistence JSON (default: <output>/flow_jobs.json)",
    )
    parser.add_argument(
        "--flow-mock",
        action="store_true",
        default=False,
        help="Use MockFlowProvider instead of ProductionFlowProvider (zero paid credit spend)",
    )
    parser.add_argument(
        "--flow-project-id",
        default=None,
        help="Google Flow Project UUID (default: reads from FLOW_PROJECT_ID or ~/.flow-py/config.json)",
    )
    parser.add_argument(
        "--flow-cdp-url",
        default=None,
        help="Connect to existing Chrome CDP endpoint (default: reads from FLOW_CDP_URL)",
    )
    parser.add_argument(
        "--flow-foreground-policy",
        choices=("auto", "background", "micro-foreground"),
        default=None,
        help="Flow foreground policy for Android CDP (default: reads from FLOW_FOREGROUND_POLICY or 'auto')",
    )

    # OpenAI provider controls. Secrets are read only from OPENAI_API_KEY.
    parser.add_argument("--script-model", default="gpt-5-mini")
    parser.add_argument("--image-model", default="gpt-image-1-mini")
    parser.add_argument("--image-quality", choices=("low", "medium", "high", "auto"), default="low")
    parser.add_argument("--tts-model", default="gpt-4o-mini-tts")
    parser.add_argument("--tts-voice", default="marin")
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument(
        "--style",
        choices=tuple(STYLE_OPTIONS),
        default="xianxia-cinematic",
        help="Visual style preset for AI story/image generation",
    )
    return parser


def build_factory_from_args(args: argparse.Namespace) -> VideoFactory:
    scenes = DURATION_TO_SCENES[args.duration_seconds] if args.duration_seconds else (args.scenes or 8)
    if not 2 <= scenes <= 12:
        raise ValueError("scenes must be between 2 and 12")
    renderer = FFmpegRenderer(width=args.width, height=args.height, fps=args.fps)
    if args.provider == "offline":
        return VideoFactory(
            planner=TemplateStoryPlanner(scene_count=scenes),
            tts=EspeakTTS(language=args.voice, speed=args.speed),
            image_provider=CardImageProvider(width=args.width, height=args.height),
            renderer=renderer,
        )

    if args.provider == "flow":
        from .flow_provider import (
            FlowAspectRatio,
            FlowController,
            FlowModel,
            FlowVisualProvider,
            ForegroundPolicy,
            MockFlowProvider,
            ProductionFlowProvider,
            FLOW_MODEL_MAP,
            resolve_flow_model,
        )
        storage_path = Path(args.flow_storage_path) if args.flow_storage_path else Path(args.output) / "flow_jobs.json"
        output_scenes_dir = Path(args.output) / "scenes"
        if args.flow_mock or os.getenv("AVF_FLOW_MOCK", "").lower() in ("1", "true", "yes"):
            prov = MockFlowProvider(initial_credits=200)
        else:
            project_id = getattr(args, "flow_project_id", None) or os.getenv("FLOW_PROJECT_ID")
            cdp_url = getattr(args, "flow_cdp_url", None) or os.getenv("FLOW_CDP_URL")
            foreground_policy = getattr(args, "flow_foreground_policy", None) or os.getenv("FLOW_FOREGROUND_POLICY")
            prov = ProductionFlowProvider(
                project_id=project_id,
                cdp_url=cdp_url,
                foreground_policy=foreground_policy,
            )
        controller = FlowController(
            provider=prov,
            storage_path=storage_path,
            output_dir=output_scenes_dir,
        )

        explicit_model = resolve_flow_model(args.flow_model) if args.flow_model else None
        visual_provider = FlowVisualProvider(
            controller=controller,
            model=explicit_model,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            flow_mode=args.flow_mode,
            width=args.width,
            height=args.height,
        )
        return VideoFactory(
            planner=TemplateStoryPlanner(scene_count=scenes),
            tts=EspeakTTS(language=args.voice, speed=args.speed),
            image_provider=visual_provider,
            renderer=renderer,
        )

    client = OpenAIHTTPClient()
    return VideoFactory(
        planner=OpenAIStoryPlanner(
            client=client,
            scene_count=scenes,
            model=args.script_model,
            visual_style=STYLE_OPTIONS[args.style]["prompt"],
        ),
        tts=OpenAITTS(
            client=client,
            model=args.tts_model,
            voice=args.tts_voice,
            speed=args.tts_speed,
        ),
        image_provider=OpenAIImageProvider(
            client=client,
            width=args.width,
            height=args.height,
            model=args.image_model,
            quality=args.image_quality,
            style_prompt=STYLE_OPTIONS[args.style]["prompt"],
        ),
        renderer=renderer,
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        factory = build_factory_from_args(args)
        result = factory.generate(args.topic, Path(args.output))
    except (ProviderError, ValueError, RuntimeError) as exc:
        error = {
            "status": "error",
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        if isinstance(exc, ProviderError):
            error["code"] = exc.code
            error["retryable"] = exc.retryable
        elif hasattr(exc, "failure_class") and getattr(exc, "failure_class") is not None:
            error["code"] = getattr(exc, "failure_class").value
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps({
        "status": "ok",
        "provider": args.provider,
        "title": result.plan.title,
        "scenes": len(result.plan.scenes),
        "duration_seconds": round(result.timings[-1].end if result.timings else 0, 2),
        "video": str(result.video),
        "subtitles": str(result.subtitles),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
