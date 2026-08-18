from pathlib import Path


WORKFLOW = Path('.github/workflows/render-video.yml')


def test_manual_render_workflow_exposes_phone_friendly_inputs():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'name: Auto Video Factory' in text
    assert 'workflow_dispatch:' in text
    for field in ['topic:', 'duration_seconds:', 'voice:', 'style:', 'provider:']:
        assert field in text
    assert 'pull_request:' not in text


def test_workflow_uses_least_privilege_secret_and_one_day_artifact():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'contents: read' in text
    assert 'OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}' in text
    assert 'actions/upload-artifact@v4' in text
    assert 'name: auto-video-output' in text
    assert 'retention-days: 1' in text
    assert 'timeout-minutes:' in text


def test_workflow_installs_media_runtime_and_calls_project_cli():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'ffmpeg' in text
    assert 'espeak' in text
    assert 'pip install .' in text
    assert 'auto-video-factory' in text
    assert '--duration-seconds "$DURATION_SECONDS"' in text
    assert '--style "$STYLE"' in text
    assert '--tts-voice "$VOICE"' in text
    assert '--provider "$PROVIDER"' in text
    assert 'test -s output/github/video.mp4' in text


def test_openai_secret_is_step_scoped_not_exposed_to_every_action():
    text = WORKFLOW.read_text(encoding='utf-8')
    job_prefix = text.split('    steps:', 1)[0]
    assert 'OPENAI_API_KEY' not in job_prefix
    assert text.count('OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}') == 2

