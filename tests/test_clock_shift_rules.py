"""校时记录驱动的去向对账规则（交班口径）。

这些用例锁定五件事：偏差跨两段先列待证、同批可确认照常入账、改属航段后原航段
不再计数、装撤颠倒按生效时刻改判末态、同份校时记录重跑不重复修正。
"""
import pytest

from app.clock import apply_clock_corrections, parse_clock_records
from app.input import sample
from app.main import create_app
from app.pipeline import compute

LEGS = """leg,flight,start,end,destination
NX-01,NX518,2026-11-20T23:00:00+08:00,2026-11-21T00:20:00+08:00,宁波
NX-02,NX518,2026-11-21T00:20:00+08:00,2026-11-21T02:00:00+08:00,宁波
"""
MANIFEST = """tag,planned
TA,NX-02
TB,NX-02
TC,NX-02
TD,NX-01
"""
SCANS = """event,tag,flight,leg_hint,action,effective,received
EA,TA,NX518,,LOAD,2026-11-21T00:15:00+08:00,2026-11-21T00:16:00+08:00
EB,TB,NX518,,LOAD,2026-11-21T00:30:00+08:00,2026-11-21T00:31:00+08:00
EC,TC,NX518,,LOAD,2026-11-21T00:10:00+08:00,2026-11-21T00:11:00+08:00
EL,TD,NX518,,LOAD,2026-11-20T23:40:00+08:00,2026-11-20T23:41:00+08:00
EO,TD,NX518,,OFFLOAD,2026-11-20T23:50:00+08:00,2026-11-20T23:51:00+08:00
"""
CLOCK = """id,event,offset_min,offset_max
K1,EA,-5,10
K2,EB,5,5
K3,EC,20,20
K4,EL,30,30
K5,EO,-20,-20
"""


def batch(clock=CLOCK):
    files = {'scans.csv': SCANS, 'legs.csv': LEGS, 'manifest.csv': MANIFEST}
    if clock is not None:
        files['clock.csv'] = clock
    return files


def row(rows, tag):
    return next(item for item in rows if item['tag'] == tag)


def test_span_two_legs_defers_only_that_bag():
    result = compute(batch())
    assert row(result['exceptions']['rows'], 'TA')['status'] == 'pending'
    assert row(result['exceptions']['rows'], 'TB')['status'] == 'loaded'
    assert result['ledger']['counts']['pending'] == 1
    assert result['ledger']['counts']['loaded'] == 3


def test_span_candidates_are_the_two_legs():
    result = compute(batch())
    isolated = next(item for item in result['legs']['isolated'] if item['event'] == 'EA')
    assert isolated['clock_span'] is True
    assert isolated['candidates'] == ['NX-01', 'NX-02']


def test_moved_scan_is_not_counted_on_former_leg():
    result = compute(batch())
    states = [(s['leg'], s['state']) for s in result['replay']['states'] if s['tag'] == 'TC']
    assert states == [('NX-02', 'LOADED')]
    nx01 = next(item for item in result['ledger']['rows'] if item['leg'] == 'NX-01')
    assert (nx01['manifest'], nx01['loaded']) == (1, 1)


def test_reversed_load_offload_rejudges_final_state():
    before = compute(batch(clock=None))
    assert next(s for s in before['replay']['states'] if s['tag'] == 'TD')['state'] == 'OFFLOADED'
    after = compute(batch())
    assert next(s for s in after['replay']['states'] if s['tag'] == 'TD')['state'] == 'LOADED'
    assert row(after['exceptions']['rows'], 'TD')['status'] == 'loaded'


def test_same_clock_record_rerun_does_not_double_correct():
    original = compute(batch(clock=None))['scans']['rows']
    records, _ = parse_clock_records(CLOCK)
    once = apply_clock_corrections(original, records)
    twice = apply_clock_corrections(once['rows'], records)
    assert once['rows'] == twice['rows']
    assert apply_clock_corrections(original, CLOCK)['rows'] == once['rows']
    assert compute(batch())['ledger'] == compute(batch())['ledger']


@pytest.fixture
def client(tmp_path):
    return create_app(str(tmp_path / 'clock.sqlite3')).test_client()


def test_ledger_api_follows_clock(client):
    response = client.post('/api/stage/ledger', json={'files': batch()})
    assert response.status_code == 200
    assert response.json['counts']['pending'] == 1


def test_late_clock_archive_is_idempotent(client):
    run = client.post('/api/archive', json={'sample': 'dirty'}).json['id']
    first = client.post(f'/api/archive/{run}/clock', json={'clock': CLOCK}).json
    second = client.post(f'/api/archive/{run}/clock', json={'clock': CLOCK}).json
    assert first['id'] == second['id'] and second['reused'] is True
    assert client.get('/api/archive').json['count'] == 2


def test_ledger_page_renders_clock_update(client):
    run = client.post('/api/archive', json={'files': batch()}).json['id']
    html = client.get(f'/?run={run}').get_data(as_text=True)
    assert '校时关联更新' in html and '偏差跨两段' in html


def test_samples_remain_at_base_totals():
    assert compute(sample('dirty'))['ledger']['counts']['pending'] == 8
    assert compute(sample('clean'))['ledger']['counts']['loaded'] == 12
