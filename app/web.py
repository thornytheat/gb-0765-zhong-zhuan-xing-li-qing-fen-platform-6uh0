from flask import Blueprint, jsonify, render_template, request
from .clock import CLOCK_FILES
from .errors import DeskError
from .input import sample, submission
from .modules.archive.routes import store
from .modules.evidence.service import index
from .pipeline import compute
from .reasons import REASONS

DESK = {
    'scans': '扫描原件', 'legs': '航段候选', 'replay': '扫件回放', 'exceptions': '错载与漏扫',
    'ledger': '去向对账', 'clock': '校时记录', 'archive': '批次留档', 'evidence': '联程证据',
}
STAGE_KEYS = tuple(DESK)[:5] + ('clock', 'corrections', 'clock_records')

bp = Blueprint('desk', __name__)


def snapshot():
    run = request.args.get('run')
    name = request.args.get('sample', 'dirty')
    if run:
        files, result = store().get(run)
        return files, result['stages'], result['manifest'], name
    files = sample(name)
    return files, compute(files), None, name


@bp.get('/api/stage/<key>')
def stage_api(key):
    if key not in STAGE_KEYS:
        raise DeskError('NO_STAGE', '业务入口不存在', 404)
    stage_key = 'clock' if key in ('clock', 'corrections', 'clock_records') else key
    run = request.args.get('run')
    if run:
        stages = store().get(run)[1]['stages']
        if stage_key not in stages:
            raise DeskError('NO_STAGE', '该归档无校时阶段', 404)
        return jsonify(stages[stage_key])
    return jsonify(compute(sample(request.args.get('sample', 'dirty')), until=stage_key))


@bp.post('/api/stage/<key>')
def inspect_stage(key):
    if key not in STAGE_KEYS:
        raise DeskError('NO_STAGE', '业务入口不存在', 404)
    stage_key = 'clock' if key in ('clock', 'corrections', 'clock_records') else key
    files = submission(request.get_json(silent=True))
    return jsonify(compute(files, until=stage_key))


def _ledger():
    files = submission(request.get_json(silent=True))
    return jsonify(compute(files)['ledger'])


for path in ('/api/clock', '/api/corrections', '/api/clock-records',
             '/api/ledger/clock', '/api/ledger/corrections', '/api/reconcile'):
    bp.add_url_rule(path, view_func=_ledger, methods=('POST',))


@bp.get('/')
@bp.get('/desk/<key>')
def desk(key='ledger'):
    if key not in DESK:
        raise DeskError('NO_STAGE', '业务入口不存在', 404)
    files, stages, manifest, name = snapshot()
    context = dict(stages=stages, ledger=stages['ledger']['counts'], names=DESK, key=key, reasons=REASONS,
                   selected=name, manifest=manifest, archive=store().list() if key == 'archive' else [],
                   clock_files=[name_ for name_ in files if name_ in CLOCK_FILES],
                   report=index(files, dict(stages=stages, manifest=manifest)) if key == 'evidence' and manifest else None)
    return render_template('desk.html', **context)
