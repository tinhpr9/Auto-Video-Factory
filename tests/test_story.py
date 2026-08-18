from auto_video_factory.story import TemplateStoryPlanner


def test_template_story_planner_creates_eight_numbered_scenes():
    plan = TemplateStoryPlanner(scene_count=8).plan(
        "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý"
    )
    assert len(plan.scenes) == 8
    assert [scene.index for scene in plan.scenes] == list(range(1, 9))
    assert all(scene.narration.strip() for scene in plan.scenes)
    assert all(scene.visual_prompt.strip() for scene in plan.scenes)
    assert plan.title


def test_story_plan_preserves_topic_in_title():
    topic = "Kiếm tu trở lại sau một trăm năm"
    plan = TemplateStoryPlanner(scene_count=8).plan(topic)
    assert topic in plan.title
