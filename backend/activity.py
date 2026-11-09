"""Request-local activity summaries; never include private memory contents."""
from contextvars import ContextVar
from contextlib import contextmanager
from . import logging_setup as logs
from datetime import datetime, timezone
from uuid import uuid4

CURRENT = ContextVar('memory_activity', default=None)


def record(kind: str, message: str):
    logs.event('learning.' + kind, summary=message)
    items = CURRENT.get()
    if items is None:
        return
    # Coalesce repeated internal reads within a single operation.
    if any(item['kind'] == kind and item['message'] == message for item in items):
        return
    if len(items) < 40:
        items.append(dict(id=uuid4().hex, kind=kind, message=message,
                          timestamp=datetime.now(timezone.utc).isoformat()))


def group(job, operation, items):
    def last(kind, default):
        return next((i['message'] for i in reversed(items) if i['kind'] == kind), default)
    sections = {
        'short_term': last('short_term', 'Used current question and reasoning.'),
        'long_term': last('long_term', 'Used learning history; no changes.'),
        'reflection': last('reflection_summary', 'Not run for this tool call.'),
    }
    return dict(id=f"{job['event_id']}:{operation}", call_id=job['event_id'],
                kind='operation', operation=operation, sections=sections,
                message=' '.join(f"{k.replace('_', ' ')}: {v}" for k, v in sections.items()))


@contextmanager
def collect(enabled=True):
    items = []
    token = CURRENT.set(items if enabled else None)
    try:
        yield items
    finally:
        CURRENT.reset(token)
