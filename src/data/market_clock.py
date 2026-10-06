"""Context tags for research; crypto trades 24/7. Exchange holidays are not inferred."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def context(ts_ms):
    now = datetime.fromtimestamp(ts_ms/1000, timezone.utc)
    minute = now.minute
    tags = ['первые 5 минут часа' if minute < 5 else 'последние 5 минут часа' if minute >= 55 else 'внутри часа']
    for label, zone, boundaries in (
        ('NYSE', 'America/New_York', ((9,30),(16,0))),
        ('LSE', 'Europe/London', ((8,0),(16,30))),
        ('TSE', 'Asia/Tokyo', ((9,0),(11,30),(12,30),(15,30))),
    ):
        local = now.astimezone(ZoneInfo(zone))
        if local.weekday() >= 5: continue
        if any(abs(local.hour*60+local.minute-(h*60+m)) <= 15 for h,m in boundaries):
            tags.append(label+' ±15 минут границы обычной сессии (праздники не проверены)')
    return tags
