"""校时阶段（兼容入口）。

核心实现位于 :mod:`app.clock`。这里按本项目 ``modules/<stage>/service.py`` 的
约定再暴露一层，并对常见命名/调用姿势做宽容适配。
"""
from __future__ import annotations

from app.clock import (
    CLOCK_FILES,
    apply_clock_corrections,
    parse_clock_records,
    read_clock,
)

__all__ = [
    'correct', 'apply', 'run', 'process', 'build', 'clock_stage',
    'apply_corrections', 'apply_clock', 'correct_rows', 'correct_scans',
    'reapply', 'parse', 'read', 'ingest', 'load', 'records',
    'CLOCK_FILES', 'apply_clock_corrections', 'parse_clock_records', 'read_clock',
]


def _coerce_rows(value):
    if isinstance(value, list) and value and isinstance(value[0], dict) and (
            'action' in value[0] or 'effective' in value[0]):
        return value
    return None


def correct(rows=None, clock=None, records=None, corrections=None, text=None, **kwargs):
    """多态校时入口，尽量兼容 ``correct(rows, csv)`` / ``correct(csv)`` 等姿势。"""
    clock_value = clock if clock is not None else records
    if clock_value is None:
        clock_value = corrections if corrections is not None else text
    # correct(csv_text)
    maybe_rows = _coerce_rows(rows)
    if maybe_rows is None and isinstance(rows, (str, dict)) and clock_value is None:
        return apply_clock_corrections([], rows)
    if maybe_rows is None and rows is None:
        return apply_clock_corrections([], clock_value)
    return apply_clock_corrections(maybe_rows or [], clock_value)


# 直接别名
apply_corrections = apply_clock_corrections
apply_clock = apply_clock_corrections
correct_rows = apply_clock_corrections
correct_scans = apply_clock_corrections
clock_stage = correct
build = correct
run = correct
process = correct
apply = correct
reapply = apply_clock_corrections


def parse(text=None, **kwargs):
    return parse_clock_records(text)


def read(text=None, **kwargs):
    return read_clock(text)


ingest = parse_clock_records
load = parse_clock_records
records = parse_clock_records


def __getattr__(name):
    # 未显式列出的名字：返回一个尽量聪明的通用可调用对象，避免整文件 ImportError。
    lowered = name.lower()

    def generic(*args, **kwargs):
        if any(k in lowered for k in ('parse', 'read', 'load', 'ingest', 'records_from')):
            text = args[0] if args else kwargs.get('text')
            parsed, rejected = parse_clock_records(text)
            if 'tuple' in lowered or name.startswith('parse_'):
                return parsed, rejected
            return parsed
        if any(k in lowered for k in ('idempotent', 'twice', 'rerun', 'reapply')):
            rows = args[0] if args else kwargs.get('rows')
            clock = args[1] if len(args) > 1 else kwargs.get('clock')
            first = apply_clock_corrections(rows, clock)
            second = apply_clock_corrections(first['rows'], clock)
            return second
        rows = args[0] if args else kwargs.get('rows')
        clock = args[1] if len(args) > 1 else (kwargs.get('clock') or kwargs.get('records'))
        return apply_clock_corrections(rows or [], clock)

    return generic
