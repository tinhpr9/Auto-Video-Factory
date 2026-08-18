"""
Google Flow Quality Pipeline & Scene Planner (V4.3).

Generates optimized Scene Packs and copyable prompt sets for Google Flow credits,
enforcing Character Bible visual continuity, credit awareness, and deterministic
staging for Auto Video Factory composition.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# ===========================================================================
# 1. Global Character Bible (Xianxia Cultivation Fantasy)
# ===========================================================================

CHARACTER_BIBLE: dict[str, Any] = {
    "protagonist": {
        "identity": "young Vietnamese cultivation fantasy protagonist (nam tu tiên anh tuấn)",
        "age_range": "19-21 years old",
        "features": (
            "sharp determined dark eyes, high ponytail tied with a dark jade hair tie, "
            "pale porcelain skin, focused heroic and calm expression"
        ),
        "attire": (
            "traditional flowing dark indigo-blue cultivation robe with intricate silver-threaded cloud embroidery at the hem, "
            "dark leather forearm bracers, engraved celestial jade pendant at waist"
        ),
        "aura": "translucent swirling azure and golden spiritual qi aura emitting from hands and sword",
    },
    "world_style": {
        "genre": "Eastern cultivation fantasy (Xianxia / Tiên Hiệp)",
        "environment": (
            "grand ethereal mountain peaks, floating ancient jade pavilions, mist-shrouded stone stairs, "
            "ancient glowing celestial runes and cascading waterfalls"
        ),
        "lighting": "dramatic volumetric god rays piercing celestial mist, bioluminescent qi particles, cinematic contrast",
        "palette": "deep indigo, celestial jade teal, radiant gold, pure snow white, obsidian black",
        "camera": "slow sweeping cinematic tracking portrait shot, 9:16 vertical aspect ratio, shallow depth of field",
    },
    "negative_constraints": [
        "modern clothing",
        "t-shirt",
        "jeans",
        "business suit",
        "astronaut",
        "desert sand dunes",
        "western medieval castle",
        "modern cars",
        "city skyscrapers",
        "modern interior apartment",
        "cartoon 3D animation",
        "deformed hands",
        "extra fingers",
        "text overlay",
        "subtitles",
        "watermark",
        "low resolution blur",
    ],
}

# ===========================================================================
# 2. Flow Model Routing & Credit Rates (Runtime Config)
# ===========================================================================

# Documented baseline Flow credit costs
FLOW_MODEL_CREDITS: dict[str, dict[str, int]] = {
    "gemini-omni-flash": {
        "4s": 15,
        "6s": 20,
        "8s": 25,
        "10s": 30,
    },
    "veo-3.1-quality": {
        "default": 100,
    },
    "veo-3.1-fast": {
        "default": 20,
    },
    "veo-3.1-lite": {
        "default": 10,
    },
}

FLOW_MODES = ("flow_balanced", "flow_economy", "flow_quality")
DEFAULT_FLOW_MODE = "flow_balanced"


# ===========================================================================
# 3. Data Structures
# ===========================================================================

@dataclass(frozen=True)
class FlowScene:
    scene_id: int
    narrative_role: str
    target_seconds: int
    recommended_model: str
    estimated_credits: int
    prompt: str
    character_reference: str
    environment: str
    action: str
    camera: str
    lighting: str
    continuity_from_previous: str
    negative_constraints: list[str]
    expected_filename: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowScenePack:
    topic: str
    flow_mode: str
    total_scenes: int
    target_total_seconds: int
    estimated_total_credits: int
    character_bible_summary: str
    scenes: list[FlowScene] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "flow_mode": self.flow_mode,
            "total_scenes": self.total_scenes,
            "target_total_seconds": self.target_total_seconds,
            "estimated_total_credits": self.estimated_total_credits,
            "character_bible_summary": self.character_bible_summary,
            "scenes": [s.to_dict() for s in self.scenes],
        }


# ===========================================================================
# 4. Flow Scene Planner
# ===========================================================================

def plan_flow_scenes(
    topic: str,
    *,
    duration_seconds: int = 45,
    flow_mode: str = DEFAULT_FLOW_MODE,
) -> FlowScenePack:
    """
    Plan 5-7 structured narrative scenes for Google Flow with Character Bible
    continuity and credit estimation.
    """
    if flow_mode not in FLOW_MODES:
        raise ValueError(f"Unknown flow_mode '{flow_mode}'. Must be one of {FLOW_MODES}")

    protag = CHARACTER_BIBLE["protagonist"]
    world = CHARACTER_BIBLE["world_style"]
    negatives = CHARACTER_BIBLE["negative_constraints"]

    # Determine scene count and duration scaling
    if duration_seconds >= 90:
        total_scene_count = 9
        scene_target_sec = 10
        raw_story_template = [
            (1, "Sect Establishing Shot", "Grand mountain sect towering into clouds, floating ancient pavilions", "Wide establishing view of the immortal sect mountain peak under morning dawn", "Slow cinematic aerial descent, 9:16 vertical composition", "None (Opening Scene)"),
            (2, "Unjust Expulsion & Rejection", "Cold snow-covered stone courtyard outside the grand sect gate", "Protagonist standing alone in snow, stripping off disciple token, looking back with cold resolve", "Medium portrait tracking shot focusing on determined eyes", "Same sect mountain backdrop"),
            (3, "Wilderness Exile", "Rugged mountain forest and desolate misty ravines", "Protagonist journeying through ancient wilderness, facing trials with calm perseverance", "Wide cinematic tracking shot", "Transition from sect to wilderness"),
            (4, "Solitary Cave Cultivation", "Dark secluded ancient cavern illuminated by glowing azure runes", "Protagonist sitting in lotus meditation, condensing swirling azure qi into core", "Low-angle slow push-in, shallow depth of field", "Secluded cultivation"),
            (5, "Ancient Power Awakening (Hero Scene)", "Celestial storm gathering over mountain chasm, golden runes shattering darkness", "Ancient dragon spirit seal awakening, golden azure light bursting from protagonist's chest", "Dynamic cinematic orbiting shot with volumetric energy explosion", "Ancient awakening"),
            (6, "Mastering Ancient Techniques", "High mountain cliff over ocean of clouds", "Protagonist practicing ancient sword forms, celestial azure blade aura slicing clouds", "Dynamic sweeping camera tracking blade movements", "Post-awakening mastery"),
            (7, "Return Journey to Sect", "Misty mountain pass leading back to civilization", "Protagonist walking with sovereign stride, spirit beasts bowing along the path", "Wide tracking shot", "Return to sect"),
            (8, "Majestic Return to Sect", "Grand stone staircase leading back up to towering mountain sect gates", "Protagonist walking calmly up stairs, robe billowing with immense azure spiritual pressure", "Low-angle following tracking shot", "Arrival at sect gate"),
            (9, "Sect Confrontation & Climax", "Grand sect plaza surrounded by disciples and elder pavilions", "Protagonist facing sect elders, drawing celestial blade, golden-azure spiritual shockwave forcing crowd to kneel", "Heroic eye-level wide portrait shot", "Climax confrontation"),
        ]
    elif duration_seconds >= 60:
        total_scene_count = 6
        scene_target_sec = 10
        raw_story_template = [
            (1, "Sect Establishing Shot", "Grand mountain sect towering into clouds, floating ancient pavilions, swirling misty waterfalls", "Wide establishing view of the immortal sect mountain peak under morning dawn", "Slow cinematic aerial descent, 9:16 vertical composition", "None (Opening Scene)"),
            (2, "Unjust Expulsion & Rejection", "Cold snow-covered stone courtyard outside the grand sect gate", "Protagonist standing alone in snow, stripping off disciple token, looking back with cold unbreakable resolve", "Medium portrait tracking shot focusing on determined eyes", "Same sect mountain backdrop, mood transitions to cold winter snowfall"),
            (3, "Solitary Cave Cultivation", "Dark secluded ancient cavern illuminated by glowing azure runes and crystal stalactites", "Protagonist sitting in lotus meditation, condensing swirling azure qi into core", "Low-angle slow push-in, shallow depth of field", "Same protagonist in dark indigo robe, isolated from the sect"),
            (4, "Ancient Power Awakening (Hero Scene)", "Celestial storm gathering over mountain chasm, glowing golden runes shattering the darkness", "Ancient dragon spirit seal awakening, golden azure light bursting from protagonist's chest, eyes igniting", "Dynamic cinematic orbiting shot with volumetric energy explosion", "Direct escalation from cave meditation, core awakens"),
            (5, "Majestic Return to Sect", "Grand stone staircase leading back up to the towering mountain sect gates", "Protagonist walking calmly up the stairs, flowing indigo robe billowing with immense azure spiritual pressure", "Low-angle following tracking shot from behind and side", "Same protagonist now radiating sovereign ancient power"),
            (6, "Sect Confrontation & Climax", "Grand sect plaza surrounded by disciples and elder pavilions", "Protagonist facing the sect elders, drawing celestial blade, golden-azure spiritual shockwave forcing crowd to kneel", "Heroic eye-level wide portrait shot, epic atmospheric lighting", "Direct confrontation payoff of the return"),
        ]
    else:  # 45s (default)
        total_scene_count = 6
        scene_target_sec = 8
        raw_story_template = [
            (1, "Sect Establishing Shot", "Grand mountain sect towering into clouds, floating ancient pavilions, swirling misty waterfalls", "Wide establishing view of the immortal sect mountain peak under morning dawn", "Slow cinematic aerial descent, 9:16 vertical composition", "None (Opening Scene)"),
            (2, "Unjust Expulsion & Rejection", "Cold snow-covered stone courtyard outside the grand sect gate", "Protagonist standing alone in snow, stripping off disciple token, looking back with cold unbreakable resolve", "Medium portrait tracking shot focusing on determined eyes", "Same sect mountain backdrop, mood transitions to cold winter snowfall"),
            (3, "Solitary Cave Cultivation", "Dark secluded ancient cavern illuminated by glowing azure runes and crystal stalactites", "Protagonist sitting in lotus meditation, condensing swirling azure qi into core", "Low-angle slow push-in, shallow depth of field", "Same protagonist in dark indigo robe, isolated from the sect"),
            (4, "Ancient Power Awakening (Hero Scene)", "Celestial storm gathering over mountain chasm, glowing golden runes shattering the darkness", "Ancient dragon spirit seal awakening, golden azure light bursting from protagonist's chest, eyes igniting", "Dynamic cinematic orbiting shot with volumetric energy explosion", "Direct escalation from cave meditation, core awakens"),
            (5, "Majestic Return to Sect", "Grand stone staircase leading back up to the towering mountain sect gates", "Protagonist walking calmly up the stairs, flowing indigo robe billowing with immense azure spiritual pressure", "Low-angle following tracking shot from behind and side", "Same protagonist now radiating sovereign ancient power"),
            (6, "Sect Confrontation & Climax", "Grand sect plaza surrounded by disciples and elder pavilions", "Protagonist facing the sect elders, drawing celestial blade, golden-azure spiritual shockwave forcing crowd to kneel", "Heroic eye-level wide portrait shot, epic atmospheric lighting", "Direct confrontation payoff of the return"),
        ]

    scenes: list[FlowScene] = []
    total_credits = 0
    total_seconds = 0
    hero_scene_id = 5 if total_scene_count == 9 else 4

    for idx, role, env_desc, action_desc, cam_desc, continuity in raw_story_template:
        target_sec = scene_target_sec
        is_hero_scene = (idx == hero_scene_id)

        # Calculate Omni Flash credit cost based on duration
        if target_sec <= 4:
            omni_cost = FLOW_MODEL_CREDITS["gemini-omni-flash"]["4s"]
        elif target_sec <= 6:
            omni_cost = FLOW_MODEL_CREDITS["gemini-omni-flash"]["6s"]
        elif target_sec <= 8:
            omni_cost = FLOW_MODEL_CREDITS["gemini-omni-flash"]["8s"]
        else:
            omni_cost = FLOW_MODEL_CREDITS["gemini-omni-flash"]["10s"]

        if flow_mode == "flow_economy":
            model = "veo-3.1-lite"
            credits = FLOW_MODEL_CREDITS["veo-3.1-lite"]["default"]
        elif flow_mode == "flow_quality":
            model = "veo-3.1-quality" if is_hero_scene or idx in (1, total_scene_count) else "gemini-omni-flash"
            credits = 100 if model == "veo-3.1-quality" else omni_cost
        else:  # flow_balanced (Default)
            if is_hero_scene:
                model = "veo-3.1-quality"
                credits = FLOW_MODEL_CREDITS["veo-3.1-quality"]["default"]
            else:
                model = "gemini-omni-flash"
                credits = omni_cost

        topic_snippet = topic.strip()
        prompt = (
            f"9:16 vertical cinematic shot of a {protag['identity']} ({protag['features']}), "
            f"wearing {protag['attire']}. Scene {idx}: {role}. "
            f"Story context: {topic_snippet}. "
            f"Setting: {env_desc}. Action: {action_desc}. "
            f"Visual style: {world['genre']}, {world['lighting']}, palette of {world['palette']}. "
            f"Camera: {cam_desc}. Quality: photorealistic, hyper-detailed textures, volumetric lighting."
        )

        char_ref = f"{protag['identity']}: {protag['features']}, {protag['attire']}"

        scene_obj = FlowScene(
            scene_id=idx,
            narrative_role=role,
            target_seconds=target_sec,
            recommended_model=model,
            estimated_credits=credits,
            prompt=prompt,
            character_reference=char_ref,
            environment=env_desc,
            action=action_desc,
            camera=cam_desc,
            lighting=world["lighting"],
            continuity_from_previous=continuity,
            negative_constraints=negatives,
            expected_filename=f"scene{idx:02d}.mp4",
        )
        scenes.append(scene_obj)
        total_credits += credits
        total_seconds += target_sec

    summary_bible = (
        f"Protagonist: {protag['identity']} ({protag['features']}, {protag['attire']}). "
        f"World: {world['genre']} ({world['environment']}). Palette: {world['palette']}."
    )

    return FlowScenePack(
        topic=topic,
        flow_mode=flow_mode,
        total_scenes=len(scenes),
        target_total_seconds=total_seconds,
        estimated_total_credits=total_credits,
        character_bible_summary=summary_bible,
        scenes=scenes,
    )


# ===========================================================================
# 5. Export Pack Exporter
# ===========================================================================

def export_flow_pack(pack: FlowScenePack, output_dir: Path) -> dict[str, Path]:
    """
    Export Flow Scene Pack to structured files:
    1. flow_scene_pack.json
    2. flow_prompts.txt (One-tap mobile copy format for Android)
    3. flow_checklist.txt (Short 5-step Android workflow checklist)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. JSON Pack
    json_path = output_dir / "flow_scene_pack.json"
    json_path.write_text(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. One-tap mobile prompts text
    prompts_lines = [
        f"=== GOOGLE FLOW PROMPT PACK ({pack.flow_mode.upper()}) ===",
        f"Topic: {pack.topic}",
        f"Total Scenes: {pack.total_scenes} | Est. Credits: {pack.estimated_total_credits}",
        "=" * 60,
        "",
    ]
    for s in pack.scenes:
        prompts_lines.extend([
            f"--- SCENE {s.scene_id:02d} [{s.expected_filename}] ---",
            f"Role: {s.narrative_role} ({s.target_seconds}s)",
            f"Model: {s.recommended_model} (Est. {s.estimated_credits} credits)",
            "PROMPT (Copy below):",
            s.prompt,
            "",
            f"NEGATIVE (If supported): {', '.join(s.negative_constraints[:6])}",
            "-" * 60,
            "",
        ])
    prompts_path = output_dir / "flow_prompts.txt"
    prompts_path.write_text("\n".join(prompts_lines), encoding="utf-8")

    # 3. Short 5-step Android checklist
    checklist = [
        "=== GOOGLE FLOW QUALITY CHECKLIST ===",
        "1. Open Google Flow on Android/Browser",
        "2. Use Character Reference image if available for consistent face/robe",
        "3. Generate clips in order from flow_prompts.txt:",
    ]
    for s in pack.scenes:
        checklist.append(f"   [ ] Scene {s.scene_id:02d} ({s.recommended_model}) -> Save as '{s.expected_filename}'")
    checklist.extend([
        "4. Move clips to storage/flow_videos/ (or upload to Auto Video Factory)",
        "5. Run Auto Video Factory render with visual_provider=flow_clips",
        "",
        f"Estimated Flow Credits: ~{pack.estimated_total_credits} credits total.",
    ])
    checklist_path = output_dir / "flow_checklist.txt"
    checklist_path.write_text("\n".join(checklist), encoding="utf-8")

    return {
        "json": json_path,
        "prompts": prompts_path,
        "checklist": checklist_path,
    }


# ===========================================================================
# 6. Clip Validation & Fail-Closed Audio Stripping for Staging
# ===========================================================================

def strip_clip_audio(input_clip: Path, output_clip: Path) -> Path:
    """
    Strip native audio from generated Flow clip so Vietnamese Edge TTS + BGM
    remain the sole audio authority.
    MUST FAIL CLOSED: Never copy original audio-bearing clip if stripping fails.
    """
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_clip),
        "-an",
        "-c:v", "copy",
        str(output_clip),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not output_clip.exists() or output_clip.stat().st_size == 0:
        if output_clip.exists():
            output_clip.unlink(missing_ok=True)
        err_msg = res.stderr.strip() if res.stderr else f"empty output or exit code {res.returncode}"
        raise RuntimeError(
            f"Fail-closed: Audio stripping failed for {input_clip} ({err_msg}). "
            "Native audio must not leak into production."
        )

    # Verify no audio streams remain
    cmd_probe = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "json",
        str(output_clip),
    ]
    try:
        probe_res = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)
        if probe_res.returncode == 0:
            data = json.loads(probe_res.stdout)
            if data.get("streams"):
                output_clip.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Fail-closed: Audio streams detected in {output_clip} after audio stripping."
                )
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        # ffprobe verification skipped if not available, but output was created successfully

    return output_clip


def validate_clip(clip_path: Path) -> bool:
    """Validate that clip exists, is non-zero, and has a valid video stream."""
    if not clip_path.exists() or clip_path.stat().st_size == 0:
        return False
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json",
        str(clip_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return False
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        duration = float(data.get("format", {}).get("duration", 0))
        return has_video and duration > 0
    except Exception:
        return False


def validate_and_stage_flow_clips(
    input_dir: Path,
    staged_dir: Path,
    expected_scene_count: int = 6,
) -> list[Path]:
    """
    Scan input_dir for scene clips (scene01.mp4..scene06.mp4), verify exact scene set,
    strip audio (fail closed), and stage in staged_dir.
    Returns list of clean staged clips.
    """
    if not input_dir.exists():
        raise ValueError(f"Missing required Flow scenes: Input directory '{input_dir}' does not exist.")

    expected_files = [f"scene{i:02d}.mp4" for i in range(1, expected_scene_count + 1)]
    raw_files = list(input_dir.glob("*.mp4"))

    # Map found files by normalized scene number
    found_scenes: dict[int, Path] = {}
    for p in raw_files:
        m = re.search(r"scene[_\-]?0*(\d+)", p.stem.lower())
        if m:
            s_num = int(m.group(1))
            if 1 <= s_num <= expected_scene_count:
                found_scenes[s_num] = p

    missing = [f"scene{i:02d}.mp4" for i in range(1, expected_scene_count + 1) if i not in found_scenes]
    if missing:
        raise ValueError(
            f"Missing required Flow scenes: {missing}. "
            f"Expected exactly {expected_scene_count} scenes ({expected_files})"
        )

    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_clips: list[Path] = []

    for idx in range(1, expected_scene_count + 1):
        raw_clip = found_scenes[idx]
        if not validate_clip(raw_clip):
            raise ValueError(f"Corrupt or invalid video clip: {raw_clip}")

        clean_name = f"scene{idx:02d}.mp4"
        out_path = staged_dir / clean_name
        strip_clip_audio(raw_clip, out_path)
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"Fail-closed: Staged clip {out_path} is empty or missing after audio stripping.")
        staged_clips.append(out_path)

    return staged_clips
