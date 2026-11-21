"""Regression tests for the incident briefing and private voice context."""

import base64
import time
import wave
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient

import colab_app
from sentinel_loop.serve import voice
from sentinel_loop.serve.investigation import InvestigationRuns

LOGIN = ('safestral', 'temporary-voice-test-password' + 'x' * 32)
PATH = '/sentinel/voice'
RUN_KEY_HEADER = 'X-Safestral-Run-Key'


class Models:
    large_model = 'mistral-large-4-0'
    review_model = 'mistral-small-2603'
    shield_model = 'test-shield'
    timestral_model = 'test-timestral'

    def __init__(self):
        self.prompts = []

    def large_json(self, _instructions, evidence, **_options):
        if 'incident_evidence' not in evidence:
            return {'metric_id': None, 'summary': 'Review the incident report.', 'reason': 'Latency increased.',
                    'monitoring': 'Collect latency measurements.'}
        self.prompts.append(evidence)
        return {'answer': 'Here is what the supplied evidence shows.'}


def wav_audio(seconds: int) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as recording:
        recording.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        recording.writeframes(b'\x00\x00' * (24000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def website(monkeypatch):
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    monkeypatch.setenv('MISTRAL_API_KEY', 'test-key')
    monkeypatch.delenv('SENTINEL_PUBLIC_URL', raising=False)
    models = Models()
    runs = InvestigationRuns(models)
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _key: runs)
    calls = []

    def audio_request(url, **options):
        calls.append(options)
        request = httpx.Request('POST', url)
        if url.endswith('/transcriptions'):
            return httpx.Response(200, request=request, json={'text': ' What happened on July 11? '})
        audio = wav_audio(10) if options['json']['response_format'] == 'wav' else b'mp3 bytes'
        return httpx.Response(200, request=request, json={'audio_data': base64.b64encode(audio).decode()})

    monkeypatch.setattr(voice.httpx, 'post', audio_request)
    with TestClient(colab_app.build_app()) as client:
        yield client, models, calls


def test_public_briefing_and_recorded_question(website):
    client, models, calls = website
    assert client.post(f'{PATH}/briefing', json={}).status_code == 401
    client.auth = LOGIN
    assert client.post(f'{PATH}/briefing', json={}, headers={'Origin': 'https://other.example'}).status_code == 403
    briefing = client.post(f'{PATH}/briefing', json={})
    assert briefing.status_code == 200
    assert briefing.json()['briefing'] == (
        'Hi, this is Safestral. Hugging Face’s daily recovered actions peaked on July 11. Any questions?'
    )
    assert briefing.json()['source_url'].startswith('https://huggingface.co/')
    assert briefing.json()['audio_type'] == 'audio/wav'
    assert base64.b64decode(briefing.json()['audio']) == wav_audio(10)
    assert calls[0]['json']['model'] == voice.SPEECH_MODEL

    result = client.post(f'{PATH}/questions', files={'audio': ('question.webm', b'recording', 'audio/webm')},
                         data={'history': '[{"question":"What incident?","answer":"The July incident."}]'})
    assert result.status_code == 200
    assert result.json()['question'] == 'What happened on July 11?'
    assert calls[1]['data']['model'] == voice.TRANSCRIPTION_MODEL
    assert calls[1]['files']['file'] == ('question.webm', b'recording', 'audio/webm')
    prompt = models.prompts[0]
    assert prompt['prior_turns'][0]['question'] == 'What incident?'
    assert {'date': '2026-07-11', 'actions': 7677} in prompt['incident_evidence']['published_activity']
    assert calls[2]['json']['input'] == result.json()['answer']
    assert calls[2]['json']['response_format'] == 'mp3'


def test_overlong_briefing_keeps_text_and_omits_audio(website, monkeypatch):
    client, _, _ = website
    client.auth = LOGIN
    monkeypatch.setattr(voice.VoxtralAudio, 'request', lambda *_args, **_kwargs: {
        'audio_data': base64.b64encode(wav_audio(11)).decode(),
    })
    result = client.post(f'{PATH}/briefing', json={})
    assert result.status_code == 200
    assert 'briefing' in result.json() and 'audio_warning' in result.json()
    assert 'audio' not in result.json()


def test_uploaded_voice_context_requires_the_run_key_and_keeps_excerpts_private(website):
    client, models, calls = website
    client.auth = LOGIN
    response = client.post('/sentinel/investigations', files={
        'file': ('incident.md', b'Upstream timed out at 14:10. Contact operator@example.org.', 'text/markdown'),
    })
    assert response.status_code == 202
    run = response.json()
    run_path = f"/sentinel/investigations/{run['id']}"
    headers = {RUN_KEY_HEADER: run['key']}
    assert client.post(f'{PATH}/briefing', json={'run_id': run['id']}).status_code == 404
    assert client.post(f'{PATH}/questions', data={'run_id': run['id'], 'question': 'What happened?'},
                       headers={RUN_KEY_HEADER: 'wrong'}).status_code == 404
    assert not calls

    for _ in range(100):
        view = client.get(run_path, headers=headers).json()
        if view['status'] == 'complete':
            break
        time.sleep(.01)
    assert view['status'] == 'complete'
    assert 'Upstream timed out' not in str(view)
    result = client.post(f'{PATH}/questions', data={'run_id': run['id'], 'question': 'What happened?'},
                         headers=headers)
    assert result.status_code == 200
    assert models.prompts[0]['incident_evidence']['file_excerpts'] == [
        {'file': 'incident.md', 'text': 'Upstream timed out at 14:10. Contact [EMAIL].'},
    ]


def test_voice_rejects_invalid_input_and_keeps_the_text_when_playback_fails(website, monkeypatch):
    client, models, calls = website
    client.auth = LOGIN
    result = client.post(f'{PATH}/questions', files={'audio': ('x.txt', b'a', 'text/plain')})
    assert result.status_code == 415
    result = client.post(f'{PATH}/questions', files={'audio': ('x.mp4', b'a' * (voice.MAX_AUDIO_BYTES + 1), 'audio/mp4')})
    assert result.status_code == 413
    assert client.post(f'{PATH}/questions', data={'question': 'Hello', 'history': '{}'}).status_code == 422
    assert not calls and not models.prompts

    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout('test timeout')

    monkeypatch.setattr(voice.httpx, 'post', timeout)
    result = client.post(f'{PATH}/questions', data={'question': 'What happened?'})
    assert result.status_code == 200
    assert 'answer' in result.json() and 'audio_warning' in result.json()
