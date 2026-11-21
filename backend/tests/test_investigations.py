"""Exercise the public upload route and per-browser investigation state."""

import hashlib
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

import colab_app
from sentinel_loop.serve.investigation import InvestigationRuns, investigate
from sentinel_loop.serve.investigation_models import InvestigationError, InvestigationModels
from sentinel_loop.serve.voice import brief_incident

LOGIN = ('safestral', 'integration-only-' + 'x' * 32)
PATH = '/sentinel/investigations'


def archive(high, *, step=timedelta(minutes=1)):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = ['timestamp,value']
    for i in range(40):
        value = high if 27 <= i < 31 else 10
        rows.append(f'{(start + i * step).isoformat()},{value}')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zipped:
        zipped.writestr('latency.csv', '\n'.join(rows))
    return out.getvalue()


class Models:
    large_model = 'test-large'
    review_model = 'test-small'
    timestral_model = 'test-timestral'
    shield_model = 'test-shield'

    def __init__(self, concurrent=2):
        self.concurrent_plans = Barrier(concurrent)

    def large_json(self, _instructions, evidence, *, constrain_json=True):
        assert constrain_json
        self.concurrent_plans.wait(timeout=5)
        if not evidence['measured_series']:
            return {'metric_id': None, 'summary': 'Monitor latency', 'reason': 'The report describes slow requests.',
                    'monitoring': 'Collect timestamped latency observations.'}
        measured = evidence['measured_series'][0]
        return {'metric_id': measured['id'], 'summary': 'Measured latency', 'reason': 'Latency is present',
                'monitoring': f"Review departures from the forecast. Peak {measured['max']}",
                'direction': 'above', 'persistence': 2}

    def review_json(self, _instructions, evidence):
        return {'findings': [{'id': item['id'], 'decision': 'triage', 'explanation': 'Four elevated observations.',
                              'hypotheses': ['Upstream slowdown', 'Gateway queue contention'],
                              'next_check': 'Check the gateway request timings.'}
                             for item in evidence['candidates']]}

    def shield(self, _instruction, _question, _evidence):
        return .9

    def shield_many(self, instruction, question, evidence_batch):
        return [self.shield(instruction, question, evidence) for evidence in evidence_batch]

    def forecast(self, contexts):
        return np.tile([9, 10, 11], (len(contexts), 1))


def wait_for(client, run):
    for _ in range(150):
        response = client.get(f"{PATH}/{run['id']}", headers={'X-Safestral-Run-Key': run['key']})
        assert response.status_code == 200
        report = response.json()
        if report['status'] in {'complete', 'failed'}:
            return report
        time.sleep(.02)
    raise AssertionError('the investigation did not finish')


def test_same_name_uploads_remain_private_and_use_each_archives_measurements(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    runs = InvestigationRuns(Models())
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _api_key: runs)
    app = colab_app.build_app()

    with TestClient(app) as first:
        first.auth = LOGIN
        second = TestClient(app)
        second.auth = LOGIN
        assert first.get(f'{PATH}/sample', auth=None).status_code == 401
        assert first.get(f'{PATH}/sample').content.startswith(b'PK')

        def submit(client, high):
            payload = archive(high)
            response = client.post(PATH, data={'prompt': f'Look for peak {high}'},
                                   files={'file': ('same.zip', payload, 'application/zip')})
            assert response.status_code == 202, response.text
            return response.json(), hashlib.sha256(payload).hexdigest()

        with ThreadPoolExecutor(2) as pool:
            send_a = pool.submit(submit, first, 91)
            send_b = pool.submit(submit, second, 167)
            a, sha_a = send_a.result(timeout=5)
            b, sha_b = send_b.result(timeout=5)
            reports = list(pool.map(lambda pair: wait_for(*pair), [(first, a), (second, b)]))

        for report, expected, sha in zip(reports, (91, 167), (sha_a, sha_b)):
            assert report['status'] == 'complete', report
            assert report['models']['investigate'] == 'test-large'
            assert report['models']['review'] == 'test-small'
            assert report['filename'] == 'same.zip'
            assert report['result']['source']['sha256'] == sha
            assert report['result']['metrics'] == [report['result']['metric']]
            assert report['result']['metric']['source_field'] == 'value'
            assert report['result']['metric']['max'] == expected
            assert report['result']['baseline']['shield_score'] == .9
            assert report['result']['calibration_count'] == 7
            assert report['result']['candidates'][0]['peak']['observed'] == expected
            assert report['result']['candidates'][0]['status'] == 'supported'

        assert brief_incident(runs.voice_context(a['id'], a['key'])) == (
            'Hi, this is Safestral. Latency alert at 00:27 UTC on January 1.'
        )
        assert first.get(f"{PATH}/{b['id']}").status_code == 404
        assert second.get(f"{PATH}/{a['id']}", headers={'X-Safestral-Run-Key': b['key']}).status_code == 404
        assert not list(tmp_path.iterdir())

        response = first.post(PATH, files={'file': ('broken.zip', b'not a ZIP', 'application/zip')})
        assert response.status_code == 202
        report = wait_for(first, response.json())
        assert report['status'] == 'failed'
        assert report['stages']['archive']['status'] == 'failed'
        assert report['stages']['investigate']['status'] == 'pending'

        collection = io.BytesIO()
        with zipfile.ZipFile(collection, 'w') as packed:
            packed.writestr('README.txt', 'Incident uploads')
            packed.writestr('incident.zip', archive(91))
        response = first.post(PATH, files={'file': ('pack.zip', collection.getvalue(), 'application/zip')})
        assert response.status_code == 202
        rejected = wait_for(first, response.json())
        assert rejected['status'] == 'failed'
        assert rejected['stages']['archive']['status'] == 'failed'
        assert rejected['error'] == 'The ZIP contains separate incident ZIPs. Extract it and upload one incident ZIP at a time.'


def test_mistral_stream_uses_only_final_answer_chunks(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":[{"type":"thinking","thinking":[]}]}}]}',
        'data: {"choices":[{"delta":{"content":[{"type":"text","text":"{\\"answer\\":"}]}}]}',
        'data: {"choices":[{"delta":{"content":"42}"}}]}',
        'data: [DONE]',
    ]

    payloads = []

    def stream(method, _url, **kwargs):
        assert method == 'POST'
        assert kwargs['json']['stream'] is True
        payloads.append(kwargs['json'])
        return nullcontext(SimpleNamespace(is_success=True, iter_lines=lambda: iter(lines)))

    monkeypatch.setattr('sentinel_loop.serve.investigation_models.httpx.stream', stream)
    models = InvestigationModels('test-key', '5M')
    assert models.large_json('Return JSON.', {'question': 'Answer?'}) == {'answer': 42}
    assert models.review_json('Return JSON.', {'question': 'Answer?'}) == {'answer': 42}
    assert [(payload['model'], payload['reasoning_effort']) for payload in payloads] == [
        ('mistral-large-4-0', 'low'), ('mistral-small-2603', 'none'),
    ]
    assert payloads[1]['response_format'] == {'type': 'json_object'}


def test_replay_preserves_subsecond_csv_intervals(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _api_key: InvestigationRuns(Models(concurrent=1)))
    payload = archive(91, step=timedelta(milliseconds=4))

    with TestClient(colab_app.build_app()) as client:
        client.auth = LOGIN
        response = client.post(PATH, files={'file': ('subsecond.zip', payload, 'application/zip')})
        assert response.status_code == 202
        replay = wait_for(client, response.json())
        assert replay['status'] == 'complete', replay
        assert replay['stages']['detect']['status'] == 'done'
        metric = replay['result']['metric']
        assert metric['measurements'] == metric['populated_intervals'] == 40
        assert metric['interval_seconds'] == .002
        assert replay['result']['scored'] == 20
        peak = replay['result']['candidates'][0]['peak']
        assert peak['time'] == '2026-01-01T00:00:00.108000+00:00'
        assert peak['observed'] == 91


def test_report_can_be_replayed_with_separate_measurements(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _api_key: InvestigationRuns(Models(concurrent=1)))
    with zipfile.ZipFile(io.BytesIO(archive(91))) as packed:
        measurements = packed.read('latency.csv')
    report = ('incident.md', b'Gateway requests slowed down.', 'text/markdown')

    with TestClient(colab_app.build_app()) as client:
        client.auth = LOGIN
        response = client.post(PATH, files={'file': report})
        assert response.status_code == 202
        plan = wait_for(client, response.json())
        assert plan['status'] == 'complete'
        assert plan['result']['metric'] is None
        assert plan['stages']['detect']['status'] == 'skipped'

        response = client.post(PATH, data={'prompt': 'Check the gateway latency.'}, files=[
            ('file', report),
            ('attachments', ('latency.csv', measurements, 'text/csv')),
            ('attachments', ('report.txt', b'Review the gateway timings.', 'text/plain')),
        ])
        assert response.status_code == 202
        fitted = wait_for(client, response.json())
        assert fitted['status'] == 'complete'
        assert fitted['question'] == 'Check the gateway latency.'
        assert fitted['result']['source']['series_count'] == 1
        assert set(fitted['result']['source']['files']) == {
            'incident.md', 'attachments/1/latency.csv', 'attachments/2/report.txt',
        }
        assert fitted['result']['source']['sha256'] != plan['result']['source']['sha256']
        assert fitted['result']['metric']['max'] == 91
        assert fitted['result']['candidates'][0]['status'] == 'supported'


def test_reviewer_only_receives_episodes_at_or_above_the_default_threshold():
    class DisagreeingModels(Models):
        def review_json(self, instructions, evidence):
            assert evidence['shield_threshold'] == .1
            assert [candidate['id'] for candidate in evidence['candidates']] == ['alert-2', 'alert-3']
            assert evidence['operator_question'] == 'Monitor latency'
            result = super().review_json(instructions, evidence)
            result['findings'][0]['decision'] = 'dismiss'
            return result

        def shield(self, _instruction, _question, evidence):
            if 'early_segment' in evidence:
                return .4999
            return .0999 if evidence['candidate']['id'] == 'alert-1' else .1

    rows = ['timestamp,latency,errors']
    for i in range(48):
        value = 40 if i in {27, 28, 32, 33, 38, 39} else 10
        rows.append(f'{(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)).isoformat()},{value},0')
    evidence = '\n'.join(rows).encode()
    result = {}

    def report(_stage, _status, _detail, **data):
        result.update(data)

    investigate(evidence, 'Monitor latency', DisagreeingModels(concurrent=1), report, filename='metrics.csv',
                attachments=(('access.log', b'2026-01-01T00:00:00 client GET / 502 12ms'),))
    assert [(row['source'], row['source_field']) for row in result['metrics']] == [
        ('metrics.csv', 'latency'), ('metrics.csv', 'errors'),
        ('attachments/1/access.log', 'HTTP status'), ('attachments/1/access.log', 'Request duration (ms)'),
    ]
    assert result['metric'] == result['metrics'][0]
    assert result['baseline']['approved'] is False
    assert result['baseline']['shield_score'] == .4999
    assert result['padding'] == 0
    assert result['policy']['shield_threshold'] == .1
    assert [(episode['shield_score'], episode.get('review_supported'), episode['status'])
            for episode in result['candidates']] == [
        (.0999, None, 'shield_below_threshold'), (.1, False, 'needs_review'), (.1, True, 'supported'),
    ]
    assert 'review_decision' not in result['candidates'][0]
    assert result['candidates'][2]['hypotheses'] == ['Upstream slowdown', 'Gateway queue contention']


def test_candidate_threshold_can_be_reapplied_to_a_private_run(tmp_path, monkeypatch):
    class LowScoreModels(Models):
        def __init__(self):
            super().__init__(concurrent=1)
            self.review_calls = []

        def review_json(self, instructions, evidence):
            self.review_calls.append(evidence)
            return super().review_json(instructions, evidence)

        def shield(self, _instruction, _question, _evidence):
            return .28

    monkeypatch.setenv('SENTINEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    models = LowScoreModels()
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _key: InvestigationRuns(models))

    with TestClient(colab_app.build_app()) as client:
        client.auth = LOGIN
        files = {'file': ('latency.zip', archive(91), 'application/zip')}
        assert client.post(PATH, data={'shield_threshold': '1.01'}, files=files).status_code == 422
        response = client.post(PATH, data={'shield_threshold': '.4'}, files=files)
        assert response.status_code == 202
        run = response.json()
        completed = wait_for(client, run)
        original = completed['result']
        assert completed['status'] == 'complete'
        assert original['policy']['shield_threshold'] == .4
        assert original['policy']['baseline_shield_threshold'] == .5
        assert original['candidates'][0]['status'] == 'shield_below_threshold'
        assert 'review_decision' not in original['candidates'][0]
        assert completed['stages']['review']['status'] == 'skipped'
        assert not models.review_calls

        path = f"{PATH}/{run['id']}"
        headers = {'X-Safestral-Run-Key': run['key']}
        assert client.patch(path, json={'shield_threshold': .28}).status_code == 404
        assert client.patch(path, headers=headers, json={'shield_threshold': -.01}).status_code == 422
        response = client.patch(path, headers=headers, json={'shield_threshold': .28})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'queued'
        updated = wait_for(client, run)['result']
        assert updated['policy']['shield_threshold'] == .28
        assert updated['policy']['review_shield_threshold'] == .4
        assert updated['candidates'][0]['status'] == 'supported'
        assert updated['candidates'][0]['review_threshold'] == .28
        assert updated['candidates'][0]['next_check'] == 'Check the gateway request timings.'
        assert updated['trace'] == original['trace']
        assert updated['baseline'] == original['baseline']
        assert client.get(path, headers=headers).json()['result'] == updated
        assert [call['shield_threshold'] for call in models.review_calls] == [.28]

        restored = client.patch(path, headers=headers, json={'shield_threshold': .4})
        assert restored.status_code == 200
        assert restored.json()['result']['candidates'][0] == {
            **updated['candidates'][0], 'status': 'shield_below_threshold',
        }
        repeated = client.patch(path, headers=headers, json={'shield_threshold': .28})
        assert repeated.json()['status'] == 'complete'
        assert repeated.json()['result'] == updated
        assert len(models.review_calls) == 1


def test_public_timeline_uses_only_published_daily_counts(tmp_path, monkeypatch):
    class TimelineModels(Models):
        def large_json(self, instructions, evidence, *, constrain_json=True):
            assert evidence['measured_series'][0]['interval_seconds'] == 86400
            assert len(evidence['published_activity']) == 5
            return super().large_json(instructions, evidence, constrain_json=constrain_json)

        def shield(self, *_args):
            raise AssertionError('five days are insufficient for baseline calibration')

        def forecast(self, _contexts):
            raise AssertionError('five days are insufficient to forecast')

    monkeypatch.setenv('SENTINEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SENTINEL_WEB_USERNAME', LOGIN[0])
    monkeypatch.setenv('SENTINEL_WEB_PASSWORD', LOGIN[1])
    monkeypatch.setattr(colab_app, 'investigation_runs', lambda _key: InvestigationRuns(TimelineModels(concurrent=1)))

    with TestClient(colab_app.build_app()) as client:
        path = f'{PATH}/hugging-face-timeline'
        assert client.post(path, json={'shield_threshold': .5}).status_code == 401
        client.auth = LOGIN
        response = client.post(path, json={'shield_threshold': .4})
        assert response.status_code == 202, response.text
        view = wait_for(client, response.json())
        assert view['status'] == 'complete', view
        result = view['result']
        assert result['source']['public_timeline_url'].startswith('https://huggingface.co/blog/')
        assert [row['date'] for row in result['published_activity']] == [f'2026-07-{day:02}' for day in range(9, 14)]
        assert [row['actions'] for row in result['published_activity']] == [3779, 1135, 7677, 3892, 1130]
        assert result['metric']['populated_intervals'] == result['metric']['intervals'] == 5
        assert result['policy']['shield_threshold'] == .4
        assert view['stages']['detect']['status'] == 'skipped'
        assert 'trace' not in result


def test_unavailable_review_keeps_candidate_and_followup():
    class UnavailableModels(Models):
        def review_json(self, _instructions, _evidence):
            raise InvestigationError('Model returned no usable review.')

    result = {}
    stages = {}

    def report(stage, status, _detail, **data):
        result.update(data)
        stages[stage] = status

    investigate(archive(91), 'Monitor latency', UnavailableModels(concurrent=1), report)
    candidate, = result['candidates']
    assert stages['review'] == 'skipped'
    assert candidate['status'] == 'needs_review'
    assert candidate['review_decision'] == 'unavailable'
    assert candidate['peak']['observed'] == 91
    assert candidate['explanation'] and candidate['next_check']


def test_all_episodes_are_screened_and_review_batches_fail_independently():
    class BatchModels(Models):
        def __init__(self):
            super().__init__(concurrent=1)
            self.shield_batches = []
            self.review_batches = []

        def shield_many(self, _instruction, _question, evidence_batch):
            ids = [item['candidate']['id'] for item in evidence_batch]
            self.shield_batches.append(ids)
            return [.08 if candidate_id == 'alert-1' else .1 for candidate_id in ids]

        def review_json(self, instructions, evidence):
            ids = [item['id'] for item in evidence['candidates']]
            self.review_batches.append(ids)
            if 'alert-2' in ids:
                raise InvestigationError('The first review batch failed.')
            return super().review_json(instructions, evidence)

    rows = ['timestamp,latency']
    elevated = {index + offset for index in range(50, 86, 4) for offset in (0, 1)}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(100):
        rows.append(f'{(start + timedelta(minutes=index)).isoformat()},{91 if index in elevated else 10}')
    models = BatchModels()
    result, stages = {}, {}

    def report(stage, status, _detail, **data):
        result.update(data)
        stages[stage] = status

    investigate('\n'.join(rows).encode(), 'Monitor latency', models, report, filename='latency.csv')
    ids = [f'alert-{number}' for number in range(1, 10)]
    assert models.shield_batches == [ids[:4], ids[4:8], ids[8:]]
    assert models.review_batches == [ids[1:7], ids[7:]]
    assert [item['status'] for item in result['candidates']] == (
        ['shield_below_threshold'] + ['needs_review'] * 6 + ['supported'] * 2
    )
    assert stages['review'] == 'done'
