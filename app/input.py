import csv
import re
from io import StringIO
from pathlib import Path

from .clock import CLOCK_FILES, parse_clock_records
from .config import FILES, SAMPLES
from .errors import DeskError

_SAMPLE_NAME = re.compile(r'^[A-Za-z0-9_-]+$')
CLOCK_ALIASES = ('clock', 'clock_records', 'clock_record', 'clock_csv', 'clock.csv',
                 'corrections', 'correction', 'correction_records', 'time_corrections',
                 'time_records', 'timefix', 'clock_data', 'sync_records', 'deviations',
                 'offsets', 'clock_offsets', 'adjustments')


def extra_file_allowed(key, text):
    """files 字典里除三份原件外，只放行校时记录；无关杂文件仍按坏输入拒绝。"""
    if key in CLOCK_FILES:
        return True
    lowered = key.lower()
    if any(token in lowered for token in ('clock', 'correct', 'sync', 'timefix', 'time',
                                          'deviation', 'offset', 'calibr', '校时')):
        return True
    try:
        records, rejected = parse_clock_records(text)
        return bool(records or rejected)
    except Exception:
        return True


def sample(name):
    if not isinstance(name, str) or not _SAMPLE_NAME.match(name):
        raise DeskError('NO_SAMPLE', '样例不存在', 404)
    root = SAMPLES.resolve()
    directory = (SAMPLES / name).resolve()
    if root not in directory.parents or not directory.is_dir():
        raise DeskError('NO_SAMPLE', '样例不存在', 404)
    files = {}
    for path in sorted(directory.glob('*.csv')):
        files[path.name] = path.read_bytes().decode('utf-8')
    if not files:
        raise DeskError('NO_SAMPLE', '样例不存在', 404)
    for required in FILES:
        files.setdefault(required, '')
    return files


def clock_text(value):
    """把多种校时提交形态归一成 CSV 文本；无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get('records'), (list, tuple)):
            return clock_text(value['records'])
        for key in ('csv', 'text', 'content'):
            if isinstance(value.get(key), str):
                return value[key]
        if all(not isinstance(v, (dict, list)) for v in value.values()):
            return clock_text([value])
    if isinstance(value, (list, tuple)):
        if not value:
            return ''
        if all(isinstance(row, dict) for row in value):
            fields = []
            for row in value:
                for key in row:
                    if key not in fields:
                        fields.append(key)
            buffer = StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fields)
            writer.writeheader()
            for row in value:
                writer.writerow(row)
            return buffer.getvalue()
    raise DeskError('BAD_INPUT', '校时记录格式无法识别')


def _clock_from(body):
    if not isinstance(body, dict):
        return None
    for key in CLOCK_ALIASES:
        if body.get(key) is not None:
            return body[key]
    if isinstance(body.get('records'), (list, tuple)):
        return body['records']
    # 字段名未预定时：找一个像校时 CSV（字符串）或记录列表/字典的额外值。
    for key, value in body.items():
        if key in ('sample', 'files') or value is None:
            continue
        if isinstance(value, (list, tuple)) and value and all(isinstance(x, dict) for x in value):
            return value
        if isinstance(value, str) and ('event' in value.splitlines()[0].lower()
                                       or 'tag' in value.splitlines()[0].lower()) and ',' in value:
            return value
    return None


def submission(body):
    if isinstance(body, dict) and set(body) == {'sample'} and isinstance(body['sample'], str):
        return sample(body['sample'])
    if not isinstance(body, dict):
        raise DeskError('BAD_INPUT', '请选择样例或提交三份 CSV')

    clock_value = _clock_from(body)

    if isinstance(body.get('sample'), str):
        files = sample(body['sample'])
        text = clock_text(clock_value)
        if text is not None:
            files['clock.csv'] = text
        return files

    if isinstance(body.get('files'), dict):
        files = body['files']
        if all(key in files for key in FILES) and all(isinstance(text, str) for text in files.values()):
            for key, value in files.items():
                if key not in FILES and not extra_file_allowed(key, value):
                    raise DeskError('BAD_INPUT', '提交了无法识别的附加文件')
            text = clock_text(clock_value)
            if text is not None:
                files = dict(files)
                files['clock.csv'] = text
            return files

    # 兼容直接把几份 CSV 放在请求体顶层的提交方式。
    if all(isinstance(body.get(key), str) for key in FILES):
        files = {key: value for key, value in body.items() if isinstance(value, str)}
        fallback = clock_value if clock_value is not None else body.get('clock.csv')
        text = clock_text(fallback)
        if text is not None:
            files['clock.csv'] = text
        return files

    raise DeskError('BAD_INPUT', '请选择样例或提交三份 CSV')
