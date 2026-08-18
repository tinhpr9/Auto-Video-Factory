import pytest

from auto_video_factory.cli import build_factory_from_args, build_parser
from auto_video_factory.media import CardImageProvider, EspeakTTS
from auto_video_factory.openai_providers import OpenAIImageProvider, OpenAIStoryPlanner, OpenAITTS, ProviderConfigurationError
from auto_video_factory.story import TemplateStoryPlanner


def parse(*extra):
    return build_parser().parse_args(["--topic", "Một kiếm tu trở lại", *extra])


def test_cli_defaults_to_offline_provider():
    args = parse()
    factory = build_factory_from_args(args)
    assert isinstance(factory.planner, TemplateStoryPlanner)
    assert isinstance(factory.tts, EspeakTTS)
    assert isinstance(factory.image_provider, CardImageProvider)


def test_cli_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = parse("--provider", "openai")
    with pytest.raises(ProviderConfigurationError):
        build_factory_from_args(args)


def test_cli_openai_provider_wires_all_three_ai_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    args = parse("--provider", "openai", "--scenes", "3")
    factory = build_factory_from_args(args)
    assert isinstance(factory.planner, OpenAIStoryPlanner)
    assert isinstance(factory.tts, OpenAITTS)
    assert isinstance(factory.image_provider, OpenAIImageProvider)


def test_cli_duration_seconds_maps_to_scene_count():
    args = parse('--duration-seconds', '90')
    factory = build_factory_from_args(args)
    assert isinstance(factory.planner, TemplateStoryPlanner)
    assert factory.planner.scene_count == 12


def test_cli_openai_style_is_applied_to_story_and_image_providers(monkeypatch):
    from auto_video_factory.presets import STYLE_OPTIONS

    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-key')
    args = parse('--provider', 'openai', '--duration-seconds', '45', '--style', 'ink-wash')
    factory = build_factory_from_args(args)
    assert isinstance(factory.planner, OpenAIStoryPlanner)
    assert factory.planner.scene_count == 6
    assert factory.planner.visual_style == STYLE_OPTIONS['ink-wash']['prompt']
    assert isinstance(factory.image_provider, OpenAIImageProvider)
    assert factory.image_provider.style_prompt == STYLE_OPTIONS['ink-wash']['prompt']
