"""Pure planning of recent re-reads and bounded older gaps."""
from datetime import datetime, timedelta
from .records import JST, interval, merged_ranges


def plan_collection(config, states, now):
    if now.tzinfo is None:
        raise ValueError('aware current time required')
    now = now.astimezone(JST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    recent = midnight - timedelta(days=3)
    plans = []
    for target in config['targets']:
        state = next((s for s in states if (s['source'], s['account']) == (target['source'], target['account'])), {})
        initial = datetime.fromisoformat(state.get('initial_start', (midnight - timedelta(days=30)).isoformat()))
        common = {k: target[k] for k in ('source', 'account')}
        common['blocked_reason'] = state.get('blocked_reason')
        plans.append({**common, 'mode': 'recent', 'range': {'start': max(initial, recent).isoformat(), 'end': now.isoformat()}})
        cursor = initial
        for coverage in merged_ranges(state.get('covered_ranges', [])):
            a, b = interval(coverage)
            if b <= cursor:
                continue
            if a > cursor:
                break
            cursor = max(cursor, b)
        if cursor < recent:
            # Stop at the next confirmed interval instead of requesting through it.
            next_covered = [interval(r)[0] for r in state.get('covered_ranges', []) if interval(r)[0] > cursor]
            end = min([recent, cursor + timedelta(days=7), *next_covered])
            plans.append({**common, 'mode': 'backfill', 'range': {'start': cursor.isoformat(), 'end': end.isoformat()}})
    return plans
