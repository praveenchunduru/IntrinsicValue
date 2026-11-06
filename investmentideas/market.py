"""Keep regular-session quotes distinct from extended-session movements."""
from datetime import datetime, timedelta, timezone
import math

FIELDS = ('symbol', 'currency', 'exchangeTimezoneName', 'fullExchangeName', 'instrumentType',
          'longName', 'shortName', 'regularMarketPrice', 'regularMarketTime',
          'regularMarketChangePercent', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
          'regularMarketDayHigh', 'regularMarketDayLow', 'regularMarketVolume')


def market_snapshot(meta):
    if not isinstance(meta, dict):
        raise ValueError('Malformed market metadata')
    result = {key: meta[key] for key in FIELDS if key in meta}
    timestamp = meta.get('regularMarketTime')
    try:
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError('Missing quote timestamp')
        observed = datetime.fromtimestamp(timestamp, timezone.utc)
        result['quote_observed_at_utc'] = observed.isoformat()
        offset = meta.get('gmtoffset')
        if isinstance(offset, int) and not isinstance(offset, bool) and -86400 < offset < 86400:
            result['quote_observed_at_exchange_offset'] = observed.astimezone(timezone(timedelta(seconds=offset))).isoformat()
    except (ValueError, OverflowError, OSError):
        result['quote_observed_at_utc'] = None
    result['notes'] = [
        'The quote observation date may precede the research or retrieval date; state it explicitly.',
        'Price and change percent shown here are regular-session observations. Extended-session and full-day fields are excluded.',
        'No daily absolute dollar change is supplied; do not invent it or substitute a chart-window anchor price.'
    ]
    return result
