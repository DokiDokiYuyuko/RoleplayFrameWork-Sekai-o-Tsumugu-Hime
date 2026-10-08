"""Synthetic Chinese evidence matching and complete model termination checks."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mrp.engines.dsh.lorebook_agent import LorebookAgentWorker
from mrp.lorebook_generation.tools import call_tool
from mrp.shared.model_output import json_object
from mrp.shared.source_quotes import locate_quote
from mrp.world_organize.schemas import ArchiveResult, CreateJobInput
from mrp.tests.test_world_organize import archive_candidate, make_world


@pytest.mark.parametrize('quote', ['每艘船限载六人，\n暴风天禁止通航。', '每艘船限载六人，暴风天禁止通航。', '每艘船限载六人，　暴风天禁止通航。'])
def test_layout_only_quote_maps_back_to_exact_original(quote):
    source = '前文。每艘船限载六人，\r\n\r\n暴风天禁止通航。后文。'
    start, end = locate_quote(source, quote)
    assert source[start:end] == '每艘船限载六人，\r\n\r\n暴风天禁止通航。'


@pytest.mark.parametrize('quote', ['每艘船限载七人。', '每艘船不限载六人。', '每艘船限载六人。暴风天可以通航。', '每艘船限载六人……禁止通航。'])
def test_rewritten_numbers_negation_and_noncontiguous_quotes_are_rejected(quote):
    assert locate_quote('每艘船限载六人。登记后才能通行。暴风天禁止通航。', quote) is None


def test_latin_word_boundaries_are_preserved():
    assert locate_quote('There is no tax here.', 'not ax') is None
    start, end = locate_quote('There is no\r\n tax here.', 'no tax')
    assert 'no\r\n tax' == 'There is no\r\n tax here.'[start:end]


def test_archive_normalizes_layout_then_keeps_batch_scope(mrp_client, monkeypatch):
    world = make_world(mrp_client)
    service = mrp_client.app.state.container.world_organize
    monkeypatch.setattr(service, '_schedule', lambda _: None)
    source = '玻璃运河在退潮时开放。\r\n\r\n每艘船限载六人，暴风天禁止通航。'
    job = service.create(CreateJobInput(world_id=world['id'], category='archives', source_text=source))
    snapshot = service._snapshot(job['id'])
    quote = '玻璃运河在退潮时开放。每艘船限载六人，暴风天禁止通航。'
    candidate = archive_candidate(quote=quote, body=source)
    service._store_archive_batch(job, ArchiveResult.model_validate({'archives': [candidate]}), snapshot, snapshot, 0)
    ref = job['drafts'][0]['source_refs'][0]
    assert ref['quote'] == source and source[ref['start']:ref['end']] == source
    batch = {**snapshot, 'sources': [{**snapshot['sources'][0], 'content': source[:14]}]}
    with pytest.raises(ValueError, match='本批次原文'):
        service._store_archive_batch(job, ArchiveResult.model_validate({'archives': [candidate]}), snapshot, batch, 1)


@pytest.mark.parametrize('raw', ['```json\n[{"title":"部分资料"}]\n```', '说明\n{"archives":[{"payload":{"title":"部分资料"}}', '```json\n{"title":"部分资料", "body":\n```'])
def test_model_parser_cannot_salvage_nested_partial_result(raw):
    with pytest.raises(ValueError):
        json_object(raw)


@pytest.mark.parametrize('reason', ['error', 'cancelled', 'max-steps', 'max_tokens', None])
def test_lorebook_worker_never_uses_intermediate_planning_text_as_success(tmp_path, monkeypatch, reason):
    class Harness:
        def __init__(self, config):
            pass
        def start(self):
            pass
        def run(self, *args, **kwargs):
            return SimpleNamespace(finish_reason=reason, final_response='已读取资料，接下来编写最终结果。')
        def close(self):
            pass
    class Proxy:
        base_url = 'http://127.0.0.1:9'
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            pass
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.DeepSeekHarness', Harness)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.ProviderRoutingProxy', Proxy)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.write_lorebook_agent_profile', lambda *args: None)
    worker = LorebookAgentWorker(tmp_path, tmp_path / 'skills')
    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(worker.run(job_id='synthetic', task_file=tmp_path / 'task.json', prompt='虚构测试',
            gateway='https://example.invalid/v1', model='synthetic', provider='', allow_fallbacks=False,
            sampling={}, api_key='synthetic-test-key', session_id='synthetic'))
    assert worker._harnesses == {}


@pytest.mark.parametrize('first_response', ['所有条目已检查通过，接下来输出。', '{"entries": [{"payload":{"content":"刻有"星潮"的木牌"}}]}'])
def test_worker_repairs_final_format_without_discarding_owned_session(tmp_path, monkeypatch, first_response):
    envelope = {'entries': [{'payload': {'keys': ['玻璃运河'], 'content': '玻璃运河在退潮时开放，暴风天禁止通航。'},
        'source_refs': [{'source_id': 'pasted-source', 'quote': '玻璃运河在退潮时开放，暴风天禁止通航。'}],
        'positive_examples': ['玻璃运河开放了吗？'], 'negative_examples': ['山里正在下雪。']}]}
    expected = json.dumps(envelope, ensure_ascii=False)
    calls = []
    class Harness:
        def __init__(self, config):
            pass
        def start(self):
            pass
        def run(self, prompt, *, session_id):
            calls.append((prompt, session_id))
            return SimpleNamespace(finish_reason='completed', final_response=first_response if len(calls) == 1 else expected)
        def close(self):
            pass
    class Proxy:
        base_url = 'http://127.0.0.1:9'
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            pass
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.DeepSeekHarness', Harness)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.ProviderRoutingProxy', Proxy)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.write_lorebook_agent_profile', lambda *args: None)
    worker = LorebookAgentWorker(tmp_path, tmp_path / 'skills')
    import asyncio
    result = asyncio.run(worker.run(job_id='synthetic', task_file=tmp_path / 'task.json', prompt='完整合成原文',
        gateway='https://example.invalid/v1', model='synthetic', provider='', allow_fallbacks=False,
        sampling={}, api_key='synthetic-test-key', session_id='owned-synthetic-session'))
    assert result == expected and len(calls) == 2
    assert [call[1] for call in calls] == ['owned-synthetic-session', 'owned-synthetic-session']
    assert 'Do not repeat the reading or simulation tools' in calls[1][0]
    assert worker._harnesses == {}


def test_lorebook_completed_batches_reach_organizer_result(mrp_client, monkeypatch):
    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    service = container.world_organize
    monkeypatch.setattr(service, '_schedule', lambda _: None)
    job = service.create(CreateJobInput(world_id=world['id'], category='lorebook', source_text='完整合成中文资料。'))
    job['child_job_id'] = 'synthetic-child'
    job['status'] = 'running'
    monkeypatch.setattr(container.lorebook_generation, 'get', lambda _: {'status': 'review',
        'stage': '草稿待审核', 'drafts': [], 'errors': [], 'completed_batches': [0], 'batch_total': 1})
    result = service._sync_child(job)
    assert result['completed_batches'] == [0] and result['batch_total'] == 1
    assert result['status'] == 'review' and result['generation_complete'] is True


def test_source_review_tool_reports_all_errors_and_canonical_batch_offsets(tmp_path):
    source = '玻璃运河在退潮时开放。\r\n每艘船限载六人。'
    task = tmp_path / 'batch.json'
    task.write_text(json.dumps({'sources': [{'id': 'selected', 'content': source, 'source_offset': 700}]}, ensure_ascii=False), 'utf-8')
    refs = [
        {'source_id': 'selected', 'quote': source.replace('\r\n', '')},
        {'source_id': 'selected', 'quote': '每艘船限载七人。'},
        {'source_id': 'outside-batch', 'quote': '每艘船限载六人。'},
        {'source_id': 'selected', 'quote': '玻璃运河在退潮时开放……限载六人。'},
        {'source_id': 'selected', 'quote': '每艘船不限载六人。'},
    ]
    result = call_tool(task, 'validate_source_refs', {'source_refs': refs})
    assert result['valid'] is False
    assert [row['valid'] for row in result['references']] == [True, False, False, False, False]
    assert result['references'][0] == {'index': 0, 'valid': True, 'source_id': 'selected',
                                      'quote': source, 'start': 700, 'end': 700 + len(source)}
    assert 'read_source/search_sources' in result['references'][1]['error']
    assert result['references'][2]['index'] == 2
    # Validation is read only: it does not count as reading unseen source text.
    telemetry = json.loads((tmp_path / 'telemetry.json').read_text('utf-8'))
    assert not telemetry.get('reads')


@pytest.mark.parametrize('quote', ['每艘船限载六人。', '玻璃运河' * 130], ids=['too-short', 'too-long'])
def test_source_review_tool_enforces_same_quote_lengths_as_final_envelope(tmp_path, quote):
    task = tmp_path / 'batch.json'
    task.write_text(json.dumps({'sources': [{'id': 'selected', 'content': quote}]}), 'utf-8')
    result = call_tool(task, 'validate_source_refs', {'source_refs': [{'source_id': 'selected', 'quote': quote}]})
    assert not result['valid'] and not result['references'][0]['valid']
    assert '12–500' in result['references'][0]['error']


@pytest.mark.parametrize('corrected', [True, False])
def test_worker_repairs_server_evidence_error_inside_same_session(tmp_path, monkeypatch, corrected):
    calls, validations = [], []
    good = '{"entries": []}'
    bad = '{"entries": [], "coverage_notes": ["synthetic invalid reference"]}'
    class Harness:
        def __init__(self, config):
            pass
        def start(self):
            pass
        def run(self, prompt, *, session_id):
            calls.append((prompt, session_id))
            return SimpleNamespace(finish_reason='completed', final_response=good if corrected and len(calls) == 2 else bad)
        def close(self):
            pass
    class Proxy:
        base_url = 'http://127.0.0.1:9'
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            pass
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.DeepSeekHarness', Harness)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.ProviderRoutingProxy', Proxy)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.write_lorebook_agent_profile', lambda *args: None)
    def validate(raw):
        validations.append(raw)
        if raw == bad:
            raise ValueError('第 2 个词条的引用无法定位，请复制连续原文')
    worker = LorebookAgentWorker(tmp_path, tmp_path / 'skills')
    import asyncio
    pending = worker.run(job_id='synthetic', task_file=tmp_path / 'task.json', prompt='完整合成原文',
        gateway='https://example.invalid/v1', model='synthetic', provider='', allow_fallbacks=False,
        sampling={}, api_key='synthetic-test-key', session_id='owned-session', validate_response=validate)
    if corrected:
        assert asyncio.run(pending) == good
    else:
        with pytest.raises(ValueError, match='第 2 个词条'):
            asyncio.run(pending)
    assert len(validations) == len(calls) == (2 if corrected else 3)
    assert all(row[1] == 'owned-session' for row in calls)
    assert '第 2 个词条' in calls[1][0] and 'validate_source_refs' in calls[1][0]
    assert worker._harnesses == {}


def test_archive_retry_preserves_previous_result_and_reports_all_errors(mrp_client):
    from mrp.tests.test_world_organize import install_archive_worker, start_job, wait_job, SOURCE
    world = make_world(mrp_client)
    bad_ref = archive_candidate('Canal', quote='Invented evidence that does not exist.')
    nested_ref = archive_candidate('Moth', kind='biology')
    nested_ref['payload']['source_refs'] = nested_ref.pop('source_refs')
    first = json.dumps({'archives': [bad_ref, nested_ref]})
    good = json.dumps({'archives': [archive_candidate('Canal'), archive_candidate('Moth', kind='biology')]})
    container, calls = install_archive_worker(mrp_client, lambda _p, _j, attempt: first if attempt == 1 else good)
    job = wait_job(mrp_client, start_job(mrp_client, world)['id'])
    assert job['status'] == 'review' and len(job['drafts']) == 2
    assert len(calls) == 2 and first in calls[1]['prompt']
    assert '第 1 条档案' in calls[1]['prompt'] and '第 2 条档案' in calls[1]['prompt']
    assert SOURCE in calls[1]['prompt'] and 'never inside payload' in calls[1]['prompt']
    # The failed model response is preserved privately even after successful repair.
    responses = list((container.world_organize._directory(job['id']) / 'responses').glob('*.txt'))
    assert len(responses) == 2 and any(path.read_text('utf-8') == first for path in responses)


def test_repeated_lorebook_resume_owns_fresh_persistent_sessions(mrp_client):
    from pathlib import Path
    from mrp.tests.test_world_organize import ENDPOINT, start_job, wait_job
    from mrp.tests.test_world_organize_lorebook import lore_candidate
    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    calls = []
    async def worker(**kwargs):
        batch = json.loads(Path(kwargs['task_file']).read_text('utf-8'))
        calls.append((kwargs['session_id'], batch['tool_session_id']))
        if len(calls) <= 4:
            raise ValueError('synthetic interrupted connection')
        raw = json.dumps({'entries': [lore_candidate(batch['sources'][0])]})
        # Run the production callback, which preserves and verifies raw output.
        kwargs['validate_response'](raw)
        return raw
    container.lorebook_generation.worker.run = worker
    job = wait_job(mrp_client, start_job(mrp_client, world, category='lorebook')['id'])
    assert job['status'] == 'failed'
    for expected in ('failed', 'review'):
        response = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume")
        assert response.status_code == 200, response.text
        job = wait_job(mrp_client, job['id'])
        assert job['status'] == expected
    assert len(calls) == 5 and len({row[0] for row in calls}) == 5
    assert all(session == scope for session, scope in calls)
    assert len(job['drafts']) == 1 and job['drafts'][0]['simulation']['valid']


def test_archive_evidence_only_retry_keeps_complete_bodies_and_valid_refs(mrp_client):
    from mrp.tests.test_world_organize import install_archive_worker, start_job, wait_job, SOURCE
    world = make_world(mrp_client)
    unchanged = archive_candidate('Valid Canal', body='Complete first archive: ' + SOURCE)
    invalid = archive_candidate('Bad Evidence', quote='A rewritten passage.', body='Complete second archive: ' + SOURCE)
    first = json.dumps({'archives': [unchanged, invalid]})
    correction = json.dumps({'repairs': [{'index': 1, 'source_refs': [{'source_id': 'pasted-source', 'quote': SOURCE[:60]}]}]})
    container, calls = install_archive_worker(mrp_client, lambda _p, _j, attempt: first if attempt == 1 else correction)
    job = wait_job(mrp_client, start_job(mrp_client, world)['id'])
    assert job['status'] == 'review' and len(job['drafts']) == 2
    assert 'Repair only the source evidence' in calls[1]['prompt'] and SOURCE in calls[1]['prompt']
    assert [row['payload']['body'] for row in job['drafts']] == [unchanged['payload']['body'], invalid['payload']['body']]
    assert job['drafts'][0]['source_refs'][0]['quote'] == SOURCE
    assert job['drafts'][1]['source_refs'][0]['quote'] == SOURCE[:60]
    persisted = json.loads((container.world_organize._directory(job['id']) / 'batch-00000-attempt-2-response.txt').read_text('utf-8'))
    assert persisted['archives'][0] == unchanged
    assert persisted['archives'][1]['payload'] == invalid['payload']


@pytest.mark.parametrize('repairs', [
    [{'index': 0, 'source_refs': [{'source_id': 'pasted-source', 'quote': 'Synthetic source'}]}],
    [{'index': 1, 'source_refs': [{'source_id': 'pasted-source', 'quote': 'Synthetic source'}]}] * 2,
    [{'index': 1, 'source_refs': [{'source_id': 'pasted-source', 'quote': 'Synthetic source'}], 'payload': {'body': 'Altered'}}],
    [],
])
def test_evidence_only_repair_cannot_change_unrequested_archive_or_body(repairs):
    from mrp.world_organize.service import WorldOrganizeService
    original = {'archives': [archive_candidate('First'), archive_candidate('Second')]}
    frozen = json.dumps(original, sort_keys=True)
    with pytest.raises(ValueError):
        WorldOrganizeService._apply_archive_reference_repairs(original, [1], json.dumps({'repairs': repairs}))
    assert json.dumps(original, sort_keys=True) == frozen


def test_archive_evidence_repair_still_rejects_outside_batch_quotes(mrp_client):
    from mrp.tests.test_world_organize import install_archive_worker, start_job, wait_job
    world = make_world(mrp_client)
    first = json.dumps({'archives': [archive_candidate(quote='Bad evidence.')]})
    correction = json.dumps({'repairs': [{'index': 0, 'source_refs': [{'source_id': 'outside-batch', 'quote': 'Bad evidence.'}]}]})
    install_archive_worker(mrp_client, lambda _p, _j, attempt: first if attempt == 1 else correction)
    job = wait_job(mrp_client, start_job(mrp_client, world)['id'])
    assert job['status'] == 'failed' and job['drafts'] == []


@pytest.mark.parametrize('input_limit', [10, 100_000])
def test_lorebook_resume_uses_saved_draft_as_correction_context(mrp_client, input_limit):
    from mrp.tests.test_world_organize import ENDPOINT, start_job, wait_job
    from mrp.tests.test_world_organize_lorebook import install_lore_worker, lore_candidate
    world = make_world(mrp_client)
    frozen = []
    def output(batch, index, count):
        entry = lore_candidate(batch['sources'][0])
        if count <= 2:
            entry['source_refs'][0]['quote'] = 'This evidence was rewritten and cannot be found.'
        raw = json.dumps({'entries': [entry]})
        frozen.append(raw)
        return raw
    container, calls = install_lore_worker(mrp_client, output)
    job = wait_job(mrp_client, start_job(mrp_client, world, category='lorebook')['id'])
    assert job['status'] == 'failed'
    parent = container.world_organize._read(job['id'])
    parent['input_limit'] = input_limit
    container.world_organize._save(parent)
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume")
    assert response.status_code == 200
    job = wait_job(mrp_client, job['id'])
    assert job['status'] == 'review' and len(job['drafts']) == 1
    assert (frozen[1] in calls[2]['prompt']) == (input_limit == 100_000)
    assert ('Preserve all its entries' in calls[2]['prompt']) == (input_limit == 100_000)


def test_worker_can_correct_format_then_schema_in_one_owned_session(tmp_path, monkeypatch):
    from mrp.lorebook_generation.validation import parse_agent_envelope
    entry = {'payload': {'keys': ['玻璃运河'], 'content': '玻璃运河在退潮时开放。'},
        'source_refs': [{'source_id': 'selected', 'quote': '玻璃运河在退潮时开放。每艘船限载六人。'}],
        'positive_examples': ['玻璃运河开放了吗？'], 'negative_examples': ['山里正在下雪。']}
    bad = json.loads(json.dumps(entry))
    bad['source_refs'][0]['quote'] = '引用太短'
    responses = ['所有词条已检查，接下来输出。', json.dumps({'entries': [bad], 'coverage_notes': '错误形状'}), json.dumps({'entries': [entry]})]
    calls = []
    class Harness:
        def __init__(self, config): pass
        def start(self): pass
        def close(self): pass
        def run(self, prompt, *, session_id):
            calls.append((prompt, session_id))
            return SimpleNamespace(finish_reason='completed', final_response=responses[len(calls) - 1])
    class Proxy:
        base_url = 'http://127.0.0.1:9'
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.DeepSeekHarness', Harness)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.ProviderRoutingProxy', Proxy)
    monkeypatch.setattr('mrp.engines.dsh.lorebook_agent.write_lorebook_agent_profile', lambda *args: None)
    worker = LorebookAgentWorker(tmp_path, tmp_path / 'skills')
    import asyncio
    result = asyncio.run(worker.run(job_id='synthetic', task_file=tmp_path / 'task.json', prompt='完整合成原文',
        gateway='https://example.invalid/v1', model='synthetic', provider='', allow_fallbacks=False,
        sampling={}, api_key='synthetic-test-key', session_id='owned-session', validate_response=parse_agent_envelope))
    assert result == responses[2] and len(calls) == 3
    assert all(session == 'owned-session' for _, session in calls)
    assert 'entries.0.source_refs.0.quote' in calls[2][0] and 'coverage_notes' in calls[2][0]
