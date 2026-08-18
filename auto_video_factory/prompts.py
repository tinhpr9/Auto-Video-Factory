from __future__ import annotations


def build_story_prompt(topic: str, scene_count: int, visual_style: str | None = None) -> str:
    topic = " ".join(topic.split()).strip()
    if not topic:
        raise ValueError("topic must not be empty")
    if not 2 <= scene_count <= 12:
        raise ValueError("scene_count must be between 2 and 12")
    style = " ".join((visual_style or "cinematic xianxia-inspired fantasy, cold cyan and slate palette").split()).strip()
    if not style:
        raise ValueError("visual_style must not be empty")

    return f"""Create an ORIGINAL Vietnamese faceless fantasy short-video story from this topic:
{topic}

Requirements:
- Exactly {scene_count} scenes.
- Total narration should feel natural for roughly 45-60 seconds when {scene_count} is around 8 scenes.
- Narration must be Vietnamese, concise, dramatic, easy to understand, and suitable for general audiences.
- Keep violence non-graphic.
- Use an original xianxia-inspired fantasy atmosphere, but do not copy named characters, franchises, dialogue, or distinctive copyrighted designs.
- Make a single concise character_anchor describing the protagonist's stable visual identity (age range, hair, clothing, one signature accessory). Keep it generic and original.
- Every scene needs one narration string and one English visual_prompt describing composition, environment, lighting, action, and camera framing.
- The visual prompts must remain visually consistent with the same protagonist.
- Visual style for every scene: {style}. Keep it original and do not imitate a named living artist.
- Scene 1 must hook immediately. The last scene must end with a cliffhanger or strong payoff.
- Do not include markdown fences or commentary.

Return JSON only with this exact shape:
{{
  "title": "Vietnamese title",
  "character_anchor": "stable English protagonist description",
  "scenes": [
    {{"narration": "Vietnamese narration", "visual_prompt": "English visual direction"}}
  ]
}}
"""
