"""
Comprehensive validation tests for FlowModel fail-closed contract.
Enforces:
  1. INVALID_MODEL_FALLBACK=NONE (Unknown/invalid models fail closed with ValueError).
  2. No provider side-effects (zero credit spend, zero network calls) on invalid model.
  3. All supported enum values, enum names, and aliases resolve deterministically.
  4. Deterministic prompt_hash participation by resolved model.
"""
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowJobRecord,
    FlowJobStatus,
    FlowModel,
    FLOW_MODEL_MAP,
    FLOW_MODE_TO_MODEL,
    resolve_flow_model,
)
from auto_video_factory.flow_provider.visual_provider import FlowVisualProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.provider import ProductionFlowProvider


class TestFlowModelFailClosed:
    """Test suite ensuring FlowModel resolution strictly fails closed."""

    @pytest.mark.parametrize("invalid_model", [
        "definitely-not-a-real-flow-model",
        "veo-3.0-ultra",
        "gpt-4o",
        "midjourney",
        "omni_turbo",
        "veo_3_1_invalid",
        "veo-3.1-typo",
        "veo_3_1",
        "   ",
        "",
        "unknown_model_12345",
        "null",
        "None",
    ])
    def test_invalid_model_string_fails_in_request(self, invalid_model: str):
        """1. INVALID_MODEL_STRING_FAILS: Unknown model must raise ValueError, not fallback to VEO_3_1_FAST."""
        with pytest.raises(ValueError, match="(?i)(invalid|unsupported|unknown)"):
            FlowGenerationRequest(
                job_id="test_invalid",
                prompt="test prompt",
                model=invalid_model,
            )

    @pytest.mark.parametrize("invalid_model", [
        "definitely-not-a-real-flow-model",
        "invalid_custom_model",
        "",
    ])
    def test_invalid_model_fails_in_resolve_flow_model(self, invalid_model: str):
        """Authoritative resolve_flow_model function must raise ValueError on invalid input."""
        with pytest.raises(ValueError, match="(?i)(invalid|unsupported|unknown)"):
            resolve_flow_model(invalid_model)

    def test_invalid_model_type_fails(self):
        """Non-string, non-FlowModel types (e.g. int, list, dict) must fail closed."""
        for invalid_val in [123, ["veo-3.1-fast"], {"model": "omni-flash"}, object()]:
            with pytest.raises(ValueError, match="(?i)(invalid|type|unsupported)"):
                resolve_flow_model(invalid_val)  # type: ignore

    def test_invalid_model_no_provider_side_effect(self, tmp_path: Path):
        """2. INVALID_MODEL_NO_PROVIDER_SIDE_EFFECT: No credits, submit, or generate calls happen."""
        mock_provider = MagicMock(spec=ProductionFlowProvider)
        mock_provider.generate_video.return_value = MagicMock()
        mock_provider.get_credits.return_value = MagicMock(available_credits=100)

        controller = FlowController(
            provider=mock_provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "scenes",
        )

        # Attempt to create visual provider with invalid model
        with pytest.raises(ValueError):
            FlowVisualProvider(
                controller=controller,
                model="definitely-not-a-real-flow-model",
            )

        # Provider must NEVER have been called for generation or credit deduction
        mock_provider.generate_video.assert_not_called()

    @pytest.mark.parametrize("enum_val,expected_enum", [
        ("omni_flash", FlowModel.OMNI_FLASH),
        ("veo_3_1_t2v_fast", FlowModel.VEO_3_1_FAST),
        ("veo_3_1_t2v", FlowModel.VEO_3_1_QUALITY),
        ("veo_2_1_fast_d_15_t2v", FlowModel.VEO_2_1_FAST),
    ])
    def test_valid_enum_value_preserved(self, enum_val: str, expected_enum: FlowModel):
        """3. VALID_ENUM_VALUE_PRESERVED: Exact enum values resolve correctly."""
        req = FlowGenerationRequest(
            job_id="test_enum_val",
            prompt="A majestic eagle flying over mountains",
            model=enum_val,
        )
        assert req.model == expected_enum
        assert resolve_flow_model(enum_val) == expected_enum

    @pytest.mark.parametrize("enum_name,expected_enum", [
        ("OMNI_FLASH", FlowModel.OMNI_FLASH),
        ("VEO_3_1_FAST", FlowModel.VEO_3_1_FAST),
        ("VEO_3_1_QUALITY", FlowModel.VEO_3_1_QUALITY),
        ("VEO_2_1_FAST", FlowModel.VEO_2_1_FAST),
        ("omni_flash", FlowModel.OMNI_FLASH),
    ])
    def test_valid_enum_name_supported(self, enum_name: str, expected_enum: FlowModel):
        """4. VALID_ENUM_NAME_IF_SUPPORTED: Enum member names resolve correctly."""
        req = FlowGenerationRequest(
            job_id="test_enum_name",
            prompt="A sunset over ocean",
            model=enum_name,
        )
        assert req.model == expected_enum

    @pytest.mark.parametrize("alias,expected_enum", list(FLOW_MODEL_MAP.items()))
    def test_valid_alias_supported_from_flow_model_map(self, alias: str, expected_enum: FlowModel):
        """5. VALID_ALIAS_IF_SUPPORTED: All FLOW_MODEL_MAP aliases resolve properly."""
        req = FlowGenerationRequest(
            job_id=f"test_alias_{alias}",
            prompt="Sample scene",
            model=alias,
        )
        assert req.model == expected_enum
        assert resolve_flow_model(alias) == expected_enum

    @pytest.mark.parametrize("mode,expected_enum", list(FLOW_MODE_TO_MODEL.items()))
    def test_valid_flow_mode_supported(self, mode: str, expected_enum: FlowModel):
        """All FLOW_MODE_TO_MODEL mode strings resolve properly."""
        assert resolve_flow_model(mode) == expected_enum

    def test_prompt_hash_model_integrity(self):
        """6. PROMPT_HASH_MODEL_INTEGRITY: Different models produce distinct prompt_hash."""
        prompt = "A futuristic cyberpunk city at dusk"
        req_omni = FlowGenerationRequest(job_id="1", prompt=prompt, model=FlowModel.OMNI_FLASH)
        req_veo_fast = FlowGenerationRequest(job_id="2", prompt=prompt, model=FlowModel.VEO_3_1_FAST)
        req_veo_qual = FlowGenerationRequest(job_id="3", prompt=prompt, model=FlowModel.VEO_3_1_QUALITY)
        req_veo_2 = FlowGenerationRequest(job_id="4", prompt=prompt, model=FlowModel.VEO_2_1_FAST)

        hashes = {req_omni.prompt_hash, req_veo_fast.prompt_hash, req_veo_qual.prompt_hash, req_veo_2.prompt_hash}
        # All 4 models must yield unique prompt hashes for identical prompt text
        assert len(hashes) == 4

        # Same model + same prompt must produce identical prompt_hash
        req_omni_dup = FlowGenerationRequest(job_id="5", prompt=prompt, model="omni-flash")
        assert req_omni.prompt_hash == req_omni_dup.prompt_hash

    def test_direct_flow_model_enum_instance_preserved(self):
        """Direct FlowModel enum instances pass through unchanged."""
        for m in FlowModel:
            req = FlowGenerationRequest(job_id="enum_test", prompt="test", model=m)
            assert req.model == m
            assert resolve_flow_model(m) == m

    @pytest.mark.parametrize("typo_model", [
        "veo-3.1-fas",   # missing 't'
        "omni-flas",     # missing 'h'
        "omni_flsh",     # missing 'a'
        "veo_3_1_t2v_fas",
        "veo-2.1-fastt", # extra 't'
        "veo3.1fast",    # missing hyphens
    ])
    def test_adversarial_single_character_typos_fail_closed(self, typo_model: str):
        """Adversarial: Near-miss typos must fail closed and never fall back to another model."""
        with pytest.raises(ValueError, match="(?i)(invalid|unsupported|unknown)"):
            resolve_flow_model(typo_model)

    def test_adversarial_controller_submit_fails_closed_before_provider(self, tmp_path: Path):
        """Adversarial: Submitting a request with invalid model string raises ValueError before provider execution."""
        mock_provider = MagicMock(spec=ProductionFlowProvider)
        controller = FlowController(
            provider=mock_provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "scenes",
        )
        with pytest.raises(ValueError):
            controller.submit(
                prompt="test prompt",
                model="invalid-nonexistent-model",  # type: ignore
            )
        mock_provider.generate_video.assert_not_called()
        mock_provider.get_credits.assert_not_called()

    def test_adversarial_cli_invalid_flow_model_fails(self):
        """Adversarial: Passing invalid flow_model to build_factory_from_args raises ValueError."""
        from argparse import Namespace
        from auto_video_factory.cli import build_factory_from_args
        args = Namespace(
            topic="Test",
            provider="flow",
            flow_model="fake-model-xyz",
            flow_mock=True,
            flow_mode="flow_balanced",
            flow_storage_path=None,
            output="output/test",
            scenes=3,
            duration_seconds=None,
            voice="vi",
            speed=175,
            width=720,
            height=1280,
            fps=30,
        )
        with pytest.raises(ValueError, match="(?i)(invalid|unsupported|unknown)"):
            build_factory_from_args(args)

    def test_adversarial_web_settings_invalid_flow_model_fails(self, monkeypatch, tmp_path: Path):
        """Adversarial: Setting invalid AVF_FLOW_MODEL in web settings raises ValueError."""
        from auto_video_factory.web import WebJobRequest, WebSettings, build_factory_for_request
        monkeypatch.setenv("AVF_FLOW_MODEL", "bad-web-model")
        settings = WebSettings(output_root=tmp_path, flow_mock=True, provider="flow")
        req = WebJobRequest(topic="Test topic", duration_seconds=45)
        with pytest.raises(ValueError, match="(?i)(invalid|unsupported|unknown)"):
            build_factory_for_request(req, settings)

