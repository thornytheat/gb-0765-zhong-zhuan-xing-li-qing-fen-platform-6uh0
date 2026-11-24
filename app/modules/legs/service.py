from collections import defaultdict


def associate(rows, legs):
    by_flight = defaultdict(list)
    for leg in legs.values():
        by_flight[leg['flight']].append(leg)
    linked, isolated = [], []
    clock_span = 0
    for row in rows:
        flight_legs = by_flight[row['flight']]
        lo = row.get('effective_lo', row['effective'])
        hi = row.get('effective_hi', row['effective'])
        ranged = bool(row.get('effective_lo') and row.get('effective_hi')
                      and row['effective_lo'] != row['effective_hi'])
        if ranged:
            # 偏差区间 [lo, hi] 与航段半开窗口 [start, end) 是否相交：
            # hi >= start 且 lo < end。相交的航段都是可能归属；跨两段即未决。
            possible = [leg['leg'] for leg in flight_legs
                        if hi >= leg['start'] and lo < leg['end']]
            candidates = sorted(possible)
        else:
            candidates = sorted(leg['leg'] for leg in flight_legs
                                if leg['start'] <= row['effective'] < leg['end'])
        chosen = [leg for leg in candidates if row['leg_hint'] in (None, leg)]
        evidence = dict(row, candidates=candidates)
        if len(chosen) == 1:
            linked.append(dict(evidence, leg=chosen[0]))
        else:
            if len(chosen) >= 2 and ranged:
                # 偏差范围跨了两个（或更多）航段：本件先列待证。保留
                # LEG_AMBIGUOUS 主原因以沿用既有归因，另加校时跨段标记。
                clock_span += 1
                evidence.update(clock_span=True, span_legs=chosen, clock_reason='CLOCK_SPAN')
                reason = 'LEG_AMBIGUOUS'
            else:
                reason = 'LEG_AMBIGUOUS' if chosen else 'LEG_MISSING'
            isolated.append(dict(evidence, reason=reason))
    return dict(rows=linked, isolated=isolated,
                counts=dict(linked=len(linked), unresolved=len(isolated), clock_span=clock_span))
