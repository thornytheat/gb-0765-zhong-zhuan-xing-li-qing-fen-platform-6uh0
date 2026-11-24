from flask import Blueprint, current_app, jsonify, request
from app.input import clock_text, submission
from app.pipeline import compute
from .service import archive

bp = Blueprint('archive', __name__)


def store():
    return current_app.extensions['transfer_archive']


@bp.get('/api/archive')
def history():
    runs = store().list()
    return jsonify(count=len(runs), rows=runs)


@bp.post('/api/archive')
def save():
    files = submission(request.get_json(silent=True))
    return jsonify(archive(store(), files, compute))


@bp.post('/api/archive/<run>/rerun')
def rerun(run):
    files, _ = store().get(run)
    return jsonify(archive(store(), files, compute))


@bp.post('/api/archive/<run>/clock')
@bp.post('/api/archive/<run>/corrections')
def apply_late_clock(run):
    """把晚到的同份校时记录并入原批重算并留新档；同源同记录命中唯一键，不重复修正。"""
    files, _ = store().get(run)
    body = request.get_json(silent=True) or {}
    text = clock_text(body.get('clock', body.get('clock_records', body.get('corrections'))))
    if text is None and isinstance(body.get('files'), dict):
        text = clock_text(body['files'].get('clock.csv'))
    if text is None:
        text = clock_text(body) if any(k in body for k in ('records',)) else None
    if text is None or not str(text).strip():
        from app.errors import DeskError
        raise DeskError('BAD_INPUT', '请提交校时记录')
    files = dict(files)
    files['clock.csv'] = text if isinstance(text, str) else clock_text(text)
    return jsonify(archive(store(), files, compute))


@bp.get('/api/archive/<run>')
def detail(run):
    _, result = store().get(run)
    return jsonify(id=run, manifest=result['manifest'], counts=result['stages']['ledger']['counts'])
