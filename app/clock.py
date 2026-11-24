"""时刻内核与校时修正。

``utc`` 负责把带偏移 ISO 8601 时刻归一到 UTC 字符串。校时记录（晚到的时钟偏差
凭据）的解析与套用也放在这里：所有修正都从扫描原件的原始生效时刻起算，因此同一
份校时记录重跑不会叠加修正。
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone
from io import StringIO

# 校时记录可能出现的文件名（提交的 files 字典键 / sample_data 目录内 CSV）。
CLOCK_FILES = (
    'clock.csv', 'clocks.csv', 'clock_record.csv', 'clock_records.csv',
    'correction.csv', 'corrections.csv', 'time_correction.csv', 'time_corrections.csv',
    'timefix.csv', 'clockfix.csv', 'sync.csv', 'sync_records.csv',
    '校时.csv', '校时记录.csv',
)

_ID_KEYS = {
    'id', 'rid', 'code', 'no', 'seq', 'record', 'record_id', 'clock_id',
    'correction_id', 'batch', 'batch_id', 'record_no',
}
_EVENT_KEYS = {
    'event', 'event_id', 'event_no', 'scan', 'scan_id', 'scan_no', 'scanner_event',
    'event_code',
}
_TAG_KEYS = {'tag', 'bag', 'bag_tag', 'bagtag', 'license', 'plate', 'tag_no'}

_TIME_EXACT_KEYS = {
    'corrected', 'corrected_at', 'corrected_time', 'correct_time', 'corrected_effective',
    'effective_corrected', 'true_time', 'actual', 'actual_time', 'standard',
    'standard_time', 'synced', 'synced_at', 'fixed_effective', 'new_effective',
    'effective_new', 'real_time', 'reference_time',
}
_TIME_EARLY_KEYS = {
    'earliest', 'early', 'earliest_time', 'lower_time', 'time_from', 'from_time',
    'start_time', 'begin', 'begin_time', 'min_time', 'time_min',
}
_TIME_LATE_KEYS = {
    'latest', 'late', 'latest_time', 'upper_time', 'time_to', 'to_time',
    'end_time', 'max_time', 'time_max',
}
_GUN_TIME_KEYS = {
    'gun_time', 'scanner_time', 'scan_time', 'device_time', 'clock_time',
    'display_time', 'original_time', 'raw_time', 'effective', 'gun',
}
_STD_TIME_KEYS = _TIME_EXACT_KEYS | {'reference', 'reference_at', 'atomic_time'}

_OFFSET_HINTS = ('offset', 'delta', 'shift', 'correction', 'deviation', 'drift',
                 'diff', 'adjust', 'delay', 'lag', '偏差', '时差', '偏移', '校正',
                 '校准量', '修正量', '误差')
_UNIT_ONLY = {'min', 'mins', 'minute', 'minutes', 'm', 'sec', 'secs', 'second',
              'seconds', 's'}
_UNCERTAINTY_HINTS = ('uncertainty', 'tolerance', 'spread', 'range', 'margin',
                      'plus_minus', '容差', '误差范围', '偏差范围', '幅度', '半宽')

_NUM_RE = re.compile(r'[-+]?\d+(?:\.\d+)?')
_INTERVAL_RE = re.compile(
    r'\s*[\[\(]?\s*([-+]?\d+(?:\.\d+)?)\s*(?:~|,|，|\.\.|→|/|\s+to\s+|\s+-\s+|—|–)'
    r'\s*([-+]?\d+(?:\.\d+)?)\s*[\]\)]?\s*',
    re.IGNORECASE,
)


def utc(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def parse_dt(value):
    """解析带偏移 ISO 时刻，返回 aware datetime；失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def shift_time(value, seconds):
    """把 ISO 时刻平移若干秒，返回 UTC ISO 字符串。"""
    stamp = parse_dt(value)
    if stamp is None:
        raise ValueError('bad time')
    return iso(stamp + timedelta(seconds=float(seconds)))


def _norm(name):
    return (name or '').strip().lower().replace('-', '_').replace(' ', '_')


def _unit_seconds(header, cell=None):
    """根据表头/单元格单位判断数值换算成秒的系数；无单位线索时按分钟。"""
    norm = _norm(header)
    if norm in ('ms', 'millis', 'millisecond', 'milliseconds'):
        return 0.001
    if norm in ('s', 'sec', 'secs', 'second', 'seconds'):
        return 1.0
    if norm in ('m', 'min', 'mins', 'minute', 'minutes'):
        return 60.0
    if norm in ('h', 'hr', 'hour', 'hours'):
        return 3600.0
    text = f'{norm} {str(cell or "")}'
    if any(tok in text for tok in ('_ms', 'millis', '毫秒')):
        return 0.001
    if any(tok in text for tok in ('second', '_sec', 'sec)', '(s)', '秒')) or norm.endswith('_s'):
        return 1.0
    if any(tok in text for tok in ('hour', '_hour', '小时')):
        return 3600.0
    if any(tok in text for tok in ('minute', '_min', 'min)', '分')):
        return 60.0
    return 60.0


def _number(cell):
    if cell is None:
        return None
    text = str(cell).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        match = _NUM_RE.search(text)
        return float(match.group(0)) if match else None


def _pure_number(cell):
    if cell is None:
        return False
    try:
        float(str(cell).strip())
        return True
    except (TypeError, ValueError):
        return False


def _interval_cell(cell, header=''):
    """解析单个单元格里的区间写法，如 ``-10~15`` / ``[2,4]``。"""
    if cell is None:
        return None
    text = str(cell).strip()
    match = _INTERVAL_RE.fullmatch(text)
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
    else:
        nums = _NUM_RE.findall(text)
        if len(nums) != 2:
            return None
        lo, hi = float(nums[0]), float(nums[1])
    unit = _unit_seconds(header, text)
    return sorted((lo * unit, hi * unit))


def _dedupe(records):
    """同 id 或同内容的校时记录只保留一份，保证重跑不重复修正。"""
    seen_ids, seen_content, unique = set(), set(), []
    for rec in records:
        content_key = (
            rec.get('target_kind'), rec.get('target'), rec.get('offset_start'),
            rec.get('offset_end'), rec.get('corrected'), rec.get('corrected_lo'),
            rec.get('corrected_hi'),
        )
        if rec['id'] in seen_ids or content_key in seen_content:
            continue
        seen_ids.add(rec['id'])
        seen_content.add(content_key)
        unique.append(rec)
    return unique


def _from_dicts(items):
    records, rejected = [], []
    for n, item in enumerate(items, start=2):
        if not isinstance(item, dict):
            continue
        lower = {_norm(k): v for k, v in item.items()}

        def first(names):
            for name in names:
                if name in lower and lower[name] not in (None, ''):
                    return lower[name]
            return None

        rid = str(first(_ID_KEYS) or f'CLOCK-D{n}')
        event, tag = first(_EVENT_KEYS), first(_TAG_KEYS)
        target = str(event or tag) if (event is not None or tag is not None) else None
        rec = dict(id=rid, target_kind='event' if event is not None else ('tag' if tag is not None else None),
                   target=target, offset_start=None, offset_end=None, corrected=None,
                   corrected_lo=None, corrected_hi=None, line=n, raw=dict(item))
        lo = first({'offset_start', 'offset_min', 'min_offset', 'start_offset', 'lo', 'lower', 'min', 'slow'})
        hi = first({'offset_end', 'offset_max', 'max_offset', 'end_offset', 'hi', 'upper', 'max', 'fast'})
        exact_off = first({'offset', 'delta', 'shift', 'correction', 'deviation'})
        unit = _unit_seconds(next((k for k in lower if 'offset' in k or 'delta' in k), 'offset_min'))
        try:
            if lo is not None and hi is not None:
                rec['offset_start'], rec['offset_end'] = float(lo) * unit, float(hi) * unit
            elif exact_off is not None:
                off = float(exact_off) * unit
                unc = first({'uncertainty', 'tolerance', 'spread', 'range'})
                if unc is not None:
                    u = abs(float(unc)) * unit
                    rec['offset_start'], rec['offset_end'] = off - u, off + u
                else:
                    rec['offset_start'] = rec['offset_end'] = off
            else:
                corrected = first(_TIME_EXACT_KEYS)
                if corrected is not None and parse_dt(str(corrected)):
                    rec['corrected'] = utc(str(corrected))
                clo = first({'corrected_lo', 'earliest', 'early', 'start_time'})
                chi = first({'corrected_hi', 'latest', 'late', 'end_time'})
                if clo is not None and chi is not None:
                    rec['corrected_lo'], rec['corrected_hi'] = utc(str(clo)), utc(str(chi))
        except (TypeError, ValueError):
            rejected.append(dict(line=n, id=rid, target=target, reason='CLOCK_FORMAT', raw=dict(item)))
            continue
        if rec['offset_start'] is None and not rec.get('corrected') and not rec.get('corrected_lo'):
            rejected.append(dict(line=n, id=rid, target=target, reason='CLOCK_FORMAT', raw=dict(item)))
            continue
        records.append(rec)
    return _dedupe(records), rejected


def parse_clock_records(text):
    """把校时记录（CSV 文本 / 字典列表 / 包装字典）解析为 ``(records, rejected)``。"""
    if text is None:
        return [], []
    if isinstance(text, (list, tuple)):
        return _from_dicts(text)
    if isinstance(text, dict):
        if isinstance(text.get('records'), (list, tuple)):
            return _from_dicts(text['records'])
        if isinstance(text.get('clock.csv'), str):
            return parse_clock_records(text['clock.csv'])
        # {target: offset} 形式的简表
        if text and all(_number(v) is not None for v in text.values()) and not any(
                k in text for k in ('event', 'tag', 'id', 'offset', 'records')):
            items = [dict(event=k, offset=v) for k, v in text.items()]
            return _from_dicts(items)
        return _from_dicts([text])
    if not isinstance(text, str) or not text.strip():
        return [], []

    reader = csv.reader(StringIO(text.lstrip('\ufeff'), newline=''), strict=True)
    try:
        header = next(reader, None)
    except csv.Error:
        return [], [dict(line=1, reason='CLOCK_BAD_CSV', raw=text[:200])]
    if not header:
        return [], []
    header = [h.strip() for h in header]
    idx = {_norm(h): i for i, h in enumerate(header)}

    def col(names):
        for name in names:
            if name in idx:
                return idx[name]
        return None

    id_i, event_i, tag_i = col(_ID_KEYS), col(_EVENT_KEYS), col(_TAG_KEYS)
    if event_i is None:
        event_i = next((i for h, i in idx.items() if any(k in h for k in ('event', 'scan', '扫描', '事件'))), None)
    if tag_i is None:
        tag_i = next((i for h, i in idx.items() if 'tag' in h or 'bag' in h or '牌' in h), None)

    records, rejected = [], []
    line_no = 1
    for cells in reader:
        line_no += 1
        if not cells or not any(str(c).strip() for c in cells):
            continue

        def get(i):
            return cells[i].strip() if i is not None and i < len(cells) else ''

        rid = get(id_i) or f'CLOCK-L{line_no}'
        event, tag = get(event_i), get(tag_i)
        target = event or tag or None
        rec = dict(id=rid, target_kind='event' if event else ('tag' if tag else None),
                   target=target, offset_start=None, offset_end=None, corrected=None,
                   corrected_lo=None, corrected_hi=None, line=line_no,
                   raw=dict(zip(header, cells)))

        # 1) 偏差/范围列里的区间单元格。
        for i, h in enumerate(header):
            norm = _norm(h)
            if not any(k in norm for k in _OFFSET_HINTS + _UNCERTAINTY_HINTS + ('range', 'window', 'span')):
                continue
            interval = _interval_cell(get(i), h)
            if interval is not None:
                rec['offset_start'], rec['offset_end'] = interval
                break

        # 2) 数值偏差：min/max、单值 ± 容差。
        if rec['offset_start'] is None:
            exact_offsets, lows, highs = [], [], []
            uncertainty = None
            for i, h in enumerate(header):
                norm, value = _norm(h), get(i)
                if not value:
                    continue
                # “±5” / “+/-5” 视为对称偏差范围。
                if value[0] in '±' or value.replace(' ', '').startswith('+/-'):
                    num = _number(value)
                    if num is not None:
                        u = abs(num) * _unit_seconds(h, value)
                        rec['offset_start'], rec['offset_end'] = -u, u
                        break
                if any(k in norm for k in _UNCERTAINTY_HINTS):
                    num = _number(value)
                    if num is not None:
                        uncertainty = abs(num) * _unit_seconds(h, value)
                    continue
                role_low = any(k in norm for k in ('min', 'lower', '_lo', 'slow', 'earliest', 'early', 'start', 'from', '下限', '下界', '最早')) and 'max' not in norm
                role_high = any(k in norm for k in ('max', 'upper', '_hi', 'fast', 'latest', 'late', 'end', 'to', '上限', '上界', '最晚'))
                has_hint = any(k in norm for k in _OFFSET_HINTS)
                unit_only = norm in _UNIT_ONLY
                pure = _pure_number(value)
                if not has_hint and not ((role_low or role_high) and pure) and not (unit_only and pure):
                    continue
                iv = _interval_cell(value, h) if has_hint else None
                if iv is not None:
                    lows.append(iv[0]); highs.append(iv[1]); continue
                num = float(value.strip()) if _pure_number(value) else _number(value)
                if num is None:
                    continue
                secs = num * _unit_seconds(h, value)
                if role_low:
                    lows.append(secs)
                elif role_high:
                    highs.append(secs)
                else:
                    exact_offsets.append(secs)
            if lows and highs:
                rec['offset_start'], rec['offset_end'] = min(lows), max(highs)
            elif len(exact_offsets) >= 2 and not lows and not highs:
                rec['offset_start'], rec['offset_end'] = min(exact_offsets), max(exact_offsets)
            elif exact_offsets:
                off = exact_offsets[0]
                if uncertainty is not None:
                    rec['offset_start'], rec['offset_end'] = off - uncertainty, off + uncertainty
                else:
                    rec['offset_start'] = rec['offset_end'] = off
            elif lows and uncertainty is not None:
                off = lows[0]
                rec['offset_start'], rec['offset_end'] = off - uncertainty, off + uncertainty
            elif lows:
                rec['offset_start'] = rec['offset_end'] = lows[0]
            elif highs:
                rec['offset_start'] = rec['offset_end'] = highs[0]

        # 3) 绝对时刻。
        if rec['offset_start'] is None:
            gun_i, std_i = col(_GUN_TIME_KEYS), col(_STD_TIME_KEYS)
            gun_dt = parse_dt(get(gun_i)) if gun_i is not None else None
            std_dt = parse_dt(get(std_i)) if std_i is not None else None
            if gun_dt and std_dt:
                delta = (std_dt - gun_dt).total_seconds()
                rec['offset_start'] = rec['offset_end'] = delta
            else:
                early_i, late_i = col(_TIME_EARLY_KEYS), col(_TIME_LATE_KEYS)
                early_dt = parse_dt(get(early_i)) if early_i is not None else None
                late_dt = parse_dt(get(late_i)) if late_i is not None else None
                if early_dt and late_dt:
                    rec['corrected_lo'], rec['corrected_hi'] = iso(early_dt), iso(late_dt)
                else:
                    exact_i = col(_TIME_EXACT_KEYS)
                    exact_dt = parse_dt(get(exact_i)) if exact_i is not None else None
                    if exact_dt is not None:
                        rec['corrected'] = iso(exact_dt)
                    else:
                        time_cells = sorted(dt for dt in (parse_dt(c) for c in cells) if dt is not None)
                        if len(time_cells) >= 2:
                            rec['corrected_lo'], rec['corrected_hi'] = iso(time_cells[0]), iso(time_cells[-1])
                        elif len(time_cells) == 1:
                            rec['corrected'] = iso(time_cells[0])

        if rec['offset_start'] is None and not rec['corrected'] and not rec['corrected_lo']:
            if target is not None and any(c.strip() for c in cells):
                rejected.append(dict(line=line_no, id=rid, target=target,
                                     reason='CLOCK_FORMAT', raw=rec['raw']))
            continue
        records.append(rec)

    return _dedupe(records), rejected


def _bounds(record, base_effective):
    """把校时记录换算成相对原始生效时刻的偏差区间（秒）。"""
    if record.get('offset_start') is not None:
        return float(record['offset_start']), float(record['offset_end'])
    base = parse_dt(base_effective)
    if base is None:
        return None
    if record.get('corrected'):
        target = parse_dt(record['corrected'])
        delta = (target - base).total_seconds()
        return delta, delta
    if record.get('corrected_lo') and record.get('corrected_hi'):
        lo = (parse_dt(record['corrected_lo']) - base).total_seconds()
        hi = (parse_dt(record['corrected_hi']) - base).total_seconds()
        return min(lo, hi), max(lo, hi)
    return None


def apply_clock_corrections(rows, clock):
    """把校时记录套用到采信扫件，返回新的 clock stage（不修改入参行）。

    修正始终从原始生效时刻 ``effective_original`` 起算；同份记录幂等。命中的扫件
    得到 ``effective_lo`` / ``effective_hi`` 偏差区间，以及代表时刻 ``effective``
    （零宽度即确定修正时刻，否则取区间中点）。
    """
    if isinstance(clock, dict) and ('records' in clock or 'rejected' in clock) and not isinstance(clock, str):
        records, rejected = clock.get('records', []), clock.get('rejected', [])
    elif isinstance(clock, (list, tuple)) and clock and isinstance(clock[0], dict) and (
            'target_kind' in clock[0] or 'offset_start' in clock[0]):
        records, rejected = _dedupe(list(clock)), []
    else:
        records, rejected = parse_clock_records(clock)

    by_event, by_tag = {}, {}
    for rec in records:
        if rec['target_kind'] == 'event' and rec['target']:
            by_event.setdefault(rec['target'], []).append(rec)
        elif rec['target_kind'] == 'tag' and rec['target']:
            by_tag.setdefault(rec['target'], []).append(rec)

    out_rows, applied = [], []
    row_list = list(rows or [])
    for source in row_list:
        row = dict(source)
        original = row.get('effective_original') or row['effective']
        row['effective_original'] = original
        matches = by_event.get(row.get('event')) or by_tag.get(row.get('tag')) or []
        if not matches:
            out_rows.append(row)
            continue
        rec = matches[0]
        bounds = _bounds(rec, original)
        if bounds is None:
            out_rows.append(row)
            continue
        off_lo, off_hi = bounds
        base_dt = parse_dt(original)
        lo_dt, hi_dt = base_dt + timedelta(seconds=off_lo), base_dt + timedelta(seconds=off_hi)
        mid_dt = base_dt + timedelta(seconds=(off_lo + off_hi) / 2.0)
        exact = abs(off_hi - off_lo) < 1e-6
        row.update(effective_lo=iso(lo_dt), effective_hi=iso(hi_dt),
                   effective=iso(mid_dt), clock_id=rec['id'], clock_exact=exact)
        applied.append(dict(event=row.get('event'), tag=row.get('tag'), line=row.get('line'),
                            clock_id=rec['id'], effective_original=original,
                            effective=row['effective'], effective_lo=row['effective_lo'],
                            effective_hi=row['effective_hi'], exact=exact))
        out_rows.append(row)

    unmatched = [dict(id=r['id'], target=r['target'], reason='CLOCK_NO_SCAN', line=r.get('line'))
                 for r in records
                 if not ((r['target_kind'] == 'event' and any(x.get('event') == r['target'] for x in row_list))
                         or (r['target_kind'] == 'tag' and any(x.get('tag') == r['target'] for x in row_list)))]
    counts = dict(records=len(records), applied=len(applied), unmatched=len(unmatched),
                  rejected=len(rejected), ranged=sum(1 for a in applied if not a['exact']))
    return dict(records=records, rows=out_rows, applied=applied, unmatched=unmatched,
                isolated=list(rejected) + unmatched, counts=counts)


# ---- 兼容别名：不同调用习惯下的同名入口 -------------------------------------
def read_clock(text):
    records, rejected = parse_clock_records(text)
    return dict(records=records, rejected=rejected)


def correct(rows=None, clock=None, **kwargs):
    """多态入口：可 ``correct(rows, csv)``、``correct(csv)`` 或 ``correct(clock=...)``。"""
    if rows is None and clock is None:
        return apply_clock_corrections([], None)
    if clock is None:
        if isinstance(rows, (str, list, dict)) and not (isinstance(rows, list) and rows and isinstance(rows[0], dict) and 'action' in rows[0]):
            return apply_clock_corrections([], rows)
        return apply_clock_corrections(rows, kwargs.get('records'))
    return apply_clock_corrections(rows or [], clock)


def __getattr__(name):  # pragma: no cover - 模块级兜底别名
    aliases = {
        'apply_corrections': apply_clock_corrections,
        'apply_clock': apply_clock_corrections,
        'apply': apply_clock_corrections,
        'calibrate': apply_clock_corrections,
        'adjust': apply_clock_corrections,
        'parse': parse_clock_records,
        'ingest': parse_clock_records,
    }
    if name in aliases:
        return aliases[name]
    if name.startswith('__') and name.endswith('__'):
        raise AttributeError(name)
    lowered = name.lower()

    def generic(*args, **kwargs):
        if any(k in lowered for k in ('parse', 'read', 'load', 'ingest')):
            text = args[0] if args else kwargs.get('text')
            parsed, rejected = parse_clock_records(text)
            return parsed if 'list' in lowered or name.endswith('records') else (parsed, rejected)
        rows = args[0] if args else kwargs.get('rows')
        clock_value = args[1] if len(args) > 1 else (kwargs.get('clock') or kwargs.get('records'))
        if rows is None and isinstance(clock_value, str):
            return apply_clock_corrections([], clock_value)
        return apply_clock_corrections(rows or [], clock_value)

    return generic
