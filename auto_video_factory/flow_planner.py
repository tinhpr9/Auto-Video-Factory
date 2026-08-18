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
# 4. Topic-Driven Scene Template Builder
# ===========================================================================

def _build_topic_scenes(
    topic: str,
    scene_count: int,
) -> list[tuple[int, str, str, str, str, str]]:
    """
    Build a scene narrative template driven by the topic string.

    Each tuple: (idx, narrative_role, env_desc, action_desc, cam_desc, continuity)

    The topic is parsed for protagonist archetype keywords and story arc signals,
    then used to inject materially different roles and actions into the template
    while keeping Character Bible visual continuity.
    """
    t = topic.lower()

    # Detect protagonist archetype from topic keywords
    if any(k in t for k in ("đan sư", "kim đan", "luyện đan", "đan lô", "đan phương")):
        archetype = "alchemist"
        awakening_role = "Divine Pill Awakening (Hero Scene)"
        awakening_env = "Ancient alchemy hall, towering Dan furnace billowing celestial fire, alchemical runes swirling"
        awakening_action = "Protagonist channeling condensed spiritual qi into Dan furnace, golden Dan vortex exploding outward, cracking the ancient seal"
        mastery_role = "Nine-Turn Dan Refinement"
        mastery_env = "Solitary stone chamber filled with medicinal cauldrons and floating spirit herbs"
        mastery_action = "Protagonist refining the Ninth-Turn Golden Dan with trembling hands, divine qi spiraling, breakthrough imminent"
        exile_role = "Framed by Alchemist Rivals & Cast Out"
        exile_action = "Protagonist stripped of alchemy hall rank token, standing outside the blazing hall doors with quiet, lethal resolve"
        return_role = "Return to Alchemy Sect with Supreme Dan"
        return_action = "Protagonist striding up jade steps carrying a glowing Dan vial, spiritual pressure forcing rival alchemists to kneel"
        climax_role = "Sect Judgment & Dan Proof"
        climax_action = "Protagonist shattering the elder's false accusation by releasing the Ninth-Turn Dan — golden celestial shockwave silencing the sect plaza"
    elif any(k in t for k in ("kiếm tu", "kiếm hồn", "kiếm đạo", "kiếm ý", "kiếm khí", "thanh kiếm")):
        archetype = "swordmaster"
        awakening_role = "Sword Soul Awakening (Hero Scene)"
        awakening_env = "Celestial mountain peak split by lightning, ancient sword seal glowing crimson-gold on stone altar"
        awakening_action = "Protagonist's sword soul erupting from collapsed dantian, divine sword intent shredding the sky, eyes blazing gold"
        mastery_role = "Heaven-Splitting Sword Intent Mastery"
        mastery_env = "High cliff over endless cloud ocean, howling celestial wind"
        mastery_action = "Protagonist executing supreme sword form, azure-gold sword qi slashing the cloud ocean in half"
        exile_role = "Expelled from Sword Sect & Cast Into Valley"
        exile_action = "Protagonist cast down the mountain steps, sword broken in front of sect gate, eyes cold and undefeated"
        return_role = "Sovereign Return to Sword Sect"
        return_action = "Protagonist ascending the sect stairs, sword soul aura radiating so powerfully that stone steps crack under each step"
        climax_role = "Sword Sect Confrontation & Dominance"
        climax_action = "Protagonist drawing ancestral sword, releasing a divine sword dao field that forces every elder and disciple to their knees"
    elif any(k in t for k in ("thể tu", "thể đạo", "thân thể", "cơ thể", "kim cương thể")):
        archetype = "body_cultivator"
        awakening_role = "Diamond Body Awakening (Hero Scene)"
        awakening_env = "Underground thunder pool, violet celestial lightning pouring down on protagonist's body"
        awakening_action = "Protagonist's diamond body activating, golden runes igniting across skin, lightning absorbed, body radiating blinding sovereignty"
        mastery_role = "Ten Thousand Ton Body Refinement"
        mastery_env = "Ancient volcano crater, rivers of molten spirit fire"
        mastery_action = "Protagonist standing in molten fire, body glowing gold, each breath drawing fire qi into the sacred marrow"
        exile_role = "Cast Out as Cripple, Sect Mark Burned Off"
        exile_action = "Protagonist kneeling as sect brand is burned from wrist, rising slowly with iron-cold conviction"
        return_role = "Unbreakable Return"
        return_action = "Protagonist walking up to the sect gate, each footstep cracking the stone plaza, golden aura forcing guards back"
        climax_role = "Body Dao Proof vs Sect Elders"
        climax_action = "Protagonist catching elder's full-force strike bare-handed, golden body unscathed, elders staggering backward in disbelief"
    else:
        # Generic cultivation archetype — extract story signals from topic keywords
        # to produce materially different return/climax arcs per topic.
        topic_phrase = topic.strip()[:60]
        t = topic.lower()
        archetype = "cultivator"

        # -- Story signal extraction --
        # Detect what the protagonist gains / wields / protects
        if any(k in t for k in ("gương", "cổ kính", "kính", "mirror", "đảo ngược thời gian")):
            story_object = "ancient time-mirror relic"
            awakening_role = "Forbidden Relic Awakening (Hero Scene)"
            awakening_env = "Shattered ancient tomb chamber, mirrors fracturing as time-reversal energy surges"
            awakening_action = (
                "Protagonist seizing the ancient mirror, forbidden time-reversal qi igniting across the chamber walls, "
                "golden-azure temporal shockwave reversing the flow of falling debris"
            )
            return_role = "Relic-Bearer's Return to Sect"
            return_action = (
                "Protagonist ascending the sect stairs with the ancient mirror radiating temporal qi, "
                "each step rewinding the stone cracks underfoot, sovereign time-aura silencing the crowd"
            )
            climax_role = "Temporal Proof vs Sect Elders"
            climax_action = (
                "Protagonist activating the ancient mirror's full power — the sect plaza frozen in a temporal bubble, "
                "elders suspended mid-strike, forced to witness the truth of the protagonist's innocence"
            )
        elif any(k in t for k in ("trứng", "linh thú", "thú", "spirit beast", "egg", "hộ vệ", "bảo vệ")):
            story_object = "spirit beast egg / bonded creature"
            awakening_role = "Beast Bond Awakening (Hero Scene)"
            awakening_env = "Burning battlefield between sect factions, protagonist shielding the spirit beast egg with their body"
            awakening_action = (
                "Spirit beast egg cracking open in a burst of violet-gold ancestral beast qi, "
                "the newborn spirit beast and protagonist merging auras in a sovereign soul-bond explosion"
            )
            return_role = "Beast-Bonded Return to Sect"
            return_action = (
                "Protagonist returning to the sect gates with the bonded spirit beast at their side, "
                "its ancient beast pressure radiating outward as disciples and guards fall to their knees"
            )
            climax_role = "Beast-Bond Judgment vs Sect Elders"
            climax_action = (
                "Protagonist releasing the spirit beast's full sovereign form — an ancestral beast manifestation "
                "towering over the sect plaza, elders prostrating before ancient bloodline authority"
            )
        elif any(k in t for k in ("bí kíp", "bí mật", "cổ thư", "secret", "tome", "forbidden", "forbidden technique")):
            story_object = "forbidden ancient tome"
            awakening_role = "Forbidden Art Awakening (Hero Scene)"
            awakening_env = "Hidden underground vault, ancient celestial seals cracking open under protagonist's qi"
            awakening_action = (
                "Protagonist absorbing the forbidden technique into their meridians, "
                "celestial script characters burning gold-azure along their arms as ancient art awakens"
            )
            return_role = "Forbidden Art Bearer's Return"
            return_action = (
                "Protagonist walking back toward the sect, forbidden art pulsing in visible celestial "
                "rune trails behind each step, the air splitting with sovereign pressure"
            )
            climax_role = "Forbidden Art Demonstration vs Elders"
            climax_action = (
                "Protagonist unleashing the forbidden technique — a celestial art vortex tearing through "
                "the sect plaza, ancient power overwhelming every elder's defensive formation"
            )
        else:
            # Deep generic: use topic phrase for exile/awakening, derive return/climax from protagonist archetype word
            story_object = topic_phrase
            awakening_role = "Ancient Power Awakening (Hero Scene)"
            awakening_env = "Celestial storm gathering over mountain chasm, glowing golden runes shattering the darkness"
            awakening_action = (
                f"Ancient dragon spirit seal awakening as protagonist channels '{topic_phrase}': "
                "golden azure light bursting from protagonist's chest, eyes igniting with sovereign will"
            )
            return_role = "Sovereign Return to Sect"
            return_action = (
                f"Protagonist returning transformed by '{topic_phrase[:40]}', "
                "flowing indigo robe billowing with immense azure spiritual pressure, "
                "the path ahead clearing as spirit energy radiates outward"
            )
            climax_role = "Sovereign Confrontation & Proof"
            climax_action = (
                f"Protagonist facing sect elders, releasing power born from '{topic_phrase[:40]}' — "
                "golden-azure spiritual shockwave forcing crowd to kneel, truth undeniable"
            )

        exile_role = "Unjust Expulsion & Rejection"
        exile_action = (
            f"Protagonist cast out, the cause rooted in '{topic_phrase}': "
            "standing alone at the sect gate, stripping off disciple token with cold unbreakable resolve"
        )
        mastery_role = f"Mastery: {story_object[:40]}"
        mastery_env = "High mountain cliff over ocean of clouds, wind howling with ancient power"
        mastery_action = (
            f"Protagonist demonstrating mastery of '{topic_phrase[:40]}': "
            "azure-gold technique aura splitting the cloud layer in a sweeping sovereign arc"
        )


    # Build 6-scene or 9-scene arc
    if scene_count == 9:
        return [
            (1, "Sect World Establishing Shot",
             "Grand mountain sect towering into clouds, floating ancient pavilions, swirling misty waterfalls",
             "Wide establishing aerial view of the immortal sect world at dawn",
             "Slow cinematic aerial descent, 9:16 vertical composition", "None (Opening Scene)"),
            (2, exile_role,
             "Cold stone courtyard outside the grand sect gate, winter mist",
             exile_action,
             "Medium portrait tracking shot, focused on determined eyes", "Sect backdrop"),
            (3, "Wilderness Exile & Trial",
             "Rugged mountain forest and desolate misty ravines, ancient spirit beasts lurking",
             "Protagonist journeying through trials with calm perseverance, spirit energy slowly recovering",
             "Wide cinematic tracking shot", "Transition from sect to wilderness"),
            (4, "Solitary Cultivation Retreat",
             "Dark secluded ancient cavern illuminated by glowing azure runes and crystal stalactites",
             "Protagonist in deep lotus meditation, condensing swirling azure qi into awakening core",
             "Low-angle slow push-in, shallow depth of field", "Isolated cultivation breakthrough building"),
            (5, awakening_role,
             awakening_env,
             awakening_action,
             "Dynamic cinematic orbiting shot with volumetric energy explosion", "Core awakening climax"),
            (6, mastery_role,
             mastery_env,
             mastery_action,
             "Dynamic sweeping camera tracking technique movements", "Post-awakening power mastery"),
            (7, "Return Journey to Sect",
             "Misty mountain pass leading back to civilization, spirit beasts parting the way",
             "Protagonist walking with sovereign stride, environment reacting to immense spiritual pressure",
             "Wide tracking shot", "Return journey begins"),
            (8, return_role,
             "Grand stone staircase leading back up to towering mountain sect gates, dawn light",
             return_action,
             "Low-angle following tracking shot from behind", "Arrival at sect gates"),
            (9, climax_role,
             "Grand sect plaza surrounded by disciples and elder pavilions, tension in the air",
             climax_action,
             "Heroic eye-level wide portrait shot, epic atmospheric lighting", "Climax confrontation payoff"),
        ]
    else:
        # 6-scene arc (45s or 60s)
        return [
            (1, "Sect World Establishing Shot",
             "Grand mountain sect towering into clouds, floating ancient pavilions, swirling misty waterfalls",
             "Wide establishing view of the immortal sect mountain peak under morning dawn",
             "Slow cinematic aerial descent, 9:16 vertical composition", "None (Opening Scene)"),
            (2, exile_role,
             "Cold snow-covered stone courtyard outside the grand sect gate",
             exile_action,
             "Medium portrait tracking shot focusing on determined eyes",
             "Same sect mountain backdrop, mood transitions to cold winter snowfall"),
            (3, "Solitary Cultivation Retreat",
             "Dark secluded ancient cavern illuminated by glowing azure runes and crystal stalactites",
             "Protagonist in deep lotus meditation, condensing swirling azure qi into awakening core",
             "Low-angle slow push-in, shallow depth of field",
             "Same protagonist in dark indigo robe, isolated from the sect"),
            (4, awakening_role,
             awakening_env,
             awakening_action,
             "Dynamic cinematic orbiting shot with volumetric energy explosion",
             "Direct escalation from cave meditation, power erupts"),
            (5, return_role,
             "Grand stone staircase leading back up to the towering mountain sect gates",
             return_action,
             "Low-angle following tracking shot from behind and side",
             "Protagonist now radiating sovereign ancient power"),
            (6, climax_role,
             "Grand sect plaza surrounded by disciples and elder pavilions",
             climax_action,
             "Heroic eye-level wide portrait shot, epic atmospheric lighting",
             "Direct confrontation payoff of the return"),
        ]


# ===========================================================================
# 5. Flow Scene Planner
# ===========================================================================

def plan_flow_scenes(
    topic: str,
    *,
    duration_seconds: int = 45,
    flow_mode: str = DEFAULT_FLOW_MODE,
) -> FlowScenePack:
    """
    Plan structured narrative scenes for Google Flow with topic-driven Character Bible
    continuity and credit estimation.

    """
    if flow_mode not in FLOW_MODES:
        raise ValueError(f"Unknown flow_mode '{flow_mode}'. Must be one of {FLOW_MODES}")

    protag = CHARACTER_BIBLE["protagonist"]
    world = CHARACTER_BIBLE["world_style"]
    negatives = CHARACTER_BIBLE["negative_constraints"]

    if duration_seconds >= 90:
        total_scene_count = 9
        scene_target_sec = 10
    elif duration_seconds >= 60:
        total_scene_count = 6
        scene_target_sec = 10
    else:  # 45s (default)
        total_scene_count = 6
        scene_target_sec = 8

    # Build topic-driven scene narrative from topic string
    raw_story_template = _build_topic_scenes(topic, total_scene_count)

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

        prompt = (
            f"9:16 vertical cinematic shot of a {protag['identity']} ({protag['features']}), "
            f"wearing {protag['attire']}. Scene {idx}: {role}. "
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

    # Verify no audio streams remain — MUST be fail-closed
    cmd_probe = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "json",
        str(output_clip),
    ]
    try:
        probe_res = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError) as exc:
        output_clip.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fail-closed: ffprobe unavailable for audio verification of {output_clip}: {exc}. "
            "Cannot confirm zero-audio state."
        ) from exc
    except subprocess.TimeoutExpired:
        output_clip.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fail-closed: ffprobe timed out verifying {output_clip}. "
            "Cannot confirm zero-audio state."
        )

    if probe_res.returncode != 0:
        output_clip.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fail-closed: ffprobe returned error (code {probe_res.returncode}) "
            f"for {output_clip}: {probe_res.stderr.strip()}."
        )

    try:
        data = json.loads(probe_res.stdout)
    except json.JSONDecodeError as exc:
        output_clip.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fail-closed: malformed ffprobe JSON output for {output_clip}: {exc}."
        ) from exc

    if data.get("streams"):
        output_clip.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fail-closed: Audio streams detected in {output_clip} after audio stripping."
        )

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
    Validate exact scene filename set (scene01.mp4..scene0N.mp4), strip audio
    fail-closed, and stage in staged_dir.

    Exact filename policy:
    - Only files named exactly scene01.mp4..scene{N:02d}.mp4 are accepted.
    - Any file whose name is not in the exact expected set raises ValueError.
    - Regex aliasing (scene_01.mp4, foo_scene01.mp4) is REJECTED.
    - Duplicate scene numbers or extra files are REJECTED.

    Returns list of clean staged clip Paths.
    """
    if not input_dir.exists():
        raise ValueError(f"Missing required Flow scenes: Input directory '{input_dir}' does not exist.")

    expected_files = {f"scene{i:02d}.mp4" for i in range(1, expected_scene_count + 1)}
    raw_files = list(input_dir.glob("*.mp4"))
    found_names = {p.name for p in raw_files}

    # Reject any file not in the exact expected set
    unexpected = found_names - expected_files
    if unexpected:
        raise ValueError(
            f"Unexpected mp4 files in input directory: {sorted(unexpected)}. "
            f"Expected exactly: {sorted(expected_files)}."
        )

    missing = sorted(expected_files - found_names)
    if missing:
        raise ValueError(
            f"Missing required Flow scenes: {missing}. "
            f"Expected exactly {expected_scene_count} scenes ({sorted(expected_files)})"
        )

    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_clips: list[Path] = []

    for idx in range(1, expected_scene_count + 1):
        exact_name = f"scene{idx:02d}.mp4"
        raw_clip = input_dir / exact_name
        if not validate_clip(raw_clip):
            raise ValueError(f"Corrupt or invalid video clip: {raw_clip}")

        out_path = staged_dir / exact_name
        strip_clip_audio(raw_clip, out_path)
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"Fail-closed: Staged clip {out_path} is empty or missing after audio stripping.")
        staged_clips.append(out_path)

    return staged_clips
