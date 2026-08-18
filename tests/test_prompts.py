from auto_video_factory.prompts import build_story_prompt


def test_story_prompt_contains_v2_contract():
    prompt = build_story_prompt("Một kiếm tu trở lại", 8)
    assert "Exactly 8 scenes" in prompt
    assert "Return JSON only" in prompt
    assert "character_anchor" in prompt
    assert "visual_prompt" in prompt
    assert "do not copy named characters" in prompt
    assert "Scene 1 must hook immediately" in prompt


def test_story_prompt_rejects_empty_topic():
    try:
        build_story_prompt("   ", 8)
    except ValueError as exc:
        assert "topic" in str(exc)
    else:
        raise AssertionError("empty topic should fail")


def test_story_prompt_rejects_out_of_range_scene_count():
    try:
        build_story_prompt("x", 1)
    except ValueError as exc:
        assert "scene_count" in str(exc)
    else:
        raise AssertionError("scene_count should fail")


def test_story_prompt_accepts_visual_style_without_copying_reference():
    prompt = build_story_prompt("Một kiếm tu trở lại", 8, visual_style="ink-wash fantasy, monochrome mist")
    assert "ink-wash fantasy" in prompt
    assert "original" in prompt.lower()
    assert "do not copy named characters" in prompt
