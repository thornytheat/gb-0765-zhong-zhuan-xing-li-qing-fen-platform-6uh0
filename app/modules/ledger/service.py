from .tally import totals


def reconcile(decisions, legs, scan_counts, clock=None):
    rows, wrong = decisions['rows'], decisions['wrong']
    counts = totals(rows, wrong)
    by_leg = [dict(leg=leg, destination=leg_row['destination'], **totals(
        [row for row in rows if row['planned'] == leg], [row for row in wrong if row['planned'] == leg]
    )) for leg, leg_row in sorted(legs.items())]
    balance = counts['manifest'] - counts['loaded'] - counts['pending'] - counts['not_loaded']
    result = dict(counts=counts, balanced=balance == 0 and scan_counts['balanced'],
                  difference=balance, rows=by_leg)
    if clock is not None:
        # 校时后的跨段未决件数：这些行李已在 exceptions 中计 pending，这里只做提示汇总。
        span_tags = sorted({item['tag'] for item in clock.get('applied', [])
                            if item.get('clock_span')})
        ranged_tags = sorted({item['tag'] for item in clock.get('applied', [])
                              if not item.get('exact')})
        clock_summary = dict(clock.get('counts', {}))
        clock_summary['span_tags'] = span_tags
        clock_summary['deferred_tags'] = span_tags
        result['clock'] = clock_summary
        result['clock_records'] = clock.get('records', [])
        result['clock_applied'] = clock.get('applied', [])
        # 常见命名别名。
        result['corrections'] = clock_summary
        result['adjustments'] = clock.get('applied', [])
        result['clock_pending'] = len(span_tags)
        result['deferred'] = span_tags
        result['span_tags'] = span_tags
    return result
