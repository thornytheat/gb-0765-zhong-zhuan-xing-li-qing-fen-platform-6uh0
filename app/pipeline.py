from app.clock import CLOCK_FILES, apply_clock_corrections, parse_clock_records
from .modules.scans.service import ingest
from .modules.legs.contracts import references
from .modules.legs.service import associate
from .modules.replay.service import replay
from .modules.exceptions.service import classify
from .modules.ledger.service import reconcile

BASE_FILES = ('scans.csv', 'legs.csv', 'manifest.csv')


def empty_clock():
    return dict(records=[], rows=None, applied=[], unmatched=[], isolated=[],
                counts=dict(records=0, applied=0, unmatched=0, rejected=0, ranged=0))


def extract_clock(files, clock=None):
    """从显式参数或 files 的额外校时文件中取校时记录。"""
    if clock is not None:
        return clock
    if not isinstance(files, dict):
        return None
    for key in CLOCK_FILES:
        value = files.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key, value in files.items():
        if key in BASE_FILES or not isinstance(value, str) or not value.strip():
            continue
        records, rejected = parse_clock_records(value)
        if records or rejected:
            return value
    return None


def compute(files, until=None, clock=None, **kwargs):
    if until in ('corrections', 'clock_records', 'timefix'):
        until = 'clock'
    if clock is None:
        for key in ('corrections', 'clock_records', 'records', 'timefix',
                    'adjustments', 'deviations', 'clock_data'):
            if kwargs.get(key) is not None:
                clock = kwargs[key]
                break
    scans = ingest(files['scans.csv'])
    if until == 'scans':
        return scans

    clock_source = extract_clock(files, clock)
    if clock_source is None:
        clock_stage = empty_clock()
        corrected_rows = scans['rows']
    else:
        clock_stage = apply_clock_corrections(scans['rows'], clock_source)
        corrected_rows = clock_stage['rows']
    if until == 'clock':
        return clock_stage

    legs, manifest, master_errors = references(files['legs.csv'], files['manifest.csv'])
    linked = associate(corrected_rows, legs)
    outcome_by_line = {}
    for row in linked['rows']:
        outcome_by_line[row.get('line')] = dict(leg=row['leg'], pending=False)
    for row in linked['isolated']:
        outcome_by_line[row.get('line')] = dict(
            leg=None, pending=True, clock_span=bool(row.get('clock_span')),
            candidates=row.get('candidates', []), reason=row.get('reason'))
    for item in clock_stage.get('applied', []):
        item.update(outcome_by_line.get(item.get('line'), dict(leg=None, pending=True)))
    linked.update(legs=list(legs.values()), manifest=list(manifest.values()), reference_isolated=master_errors)
    if until == 'legs':
        return linked
    history = replay(linked['rows'])
    if until == 'replay':
        return history
    span_tags = {row.get('tag') for row in linked['isolated'] if row.get('clock_span') and row.get('tag')}
    decisions = classify(manifest, history['states'], scans['isolated'] + linked['isolated'],
                         uncertain_tags=span_tags)
    if until == 'exceptions':
        return decisions
    ledger = reconcile(decisions, legs, scans['counts'], clock=clock_stage)
    if until == 'ledger':
        return ledger
    result = dict(scans=scans, clock=clock_stage, legs=linked, replay=history,
                  exceptions=decisions, ledger=ledger)
    # 常见命名别名，指向同一阶段对象。
    result['corrections'] = clock_stage
    result['clock_records'] = clock_stage
    return result
