"""Session-scoped JSON INFO logs, queued off the request thread."""
import json
import logging
import logging.handlers
import queue
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from . import config

CONTEXT = ContextVar('log_context', default={})
LOG = logging.getLogger('tutor')
_listener = None
_handler = None


def serializable(value):
    if hasattr(value, 'model_dump'):
        # SDK generic responses can declare parsed=None yet contain a runtime
        # Pydantic model. Serialize its actual type to preserve it without warnings.
        return value.model_dump(mode='json', serialize_as_any=True)
    if hasattr(value, '__dict__'):
        return vars(value)
    return str(value)


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.context = dict(CONTEXT.get())
        record.context.setdefault('operation_id', '-')
        record.context.setdefault('session_id', '-')
        record.context.setdefault('session_uuid', '-')
        return True


class SessionFiles(logging.Handler):
    def __init__(self, directory, run_id):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.files = OrderedDict()

    def emit(self, record):
        try:
            context = getattr(record, 'context', {})
            key = context.get('session_uuid', '-')
            filename = f'{key}.log' if key != '-' else f'startup-{self.run_id}.log'
            stream = self.files.pop(filename, None)
            if stream is None:
                stream = (self.directory / filename).open('a', encoding='utf-8')
            self.files[filename] = stream
            while len(self.files) > 32:
                self.files.popitem(last=False)[1].close()
            data = dict(timestamp=datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
                        level=record.levelname, module=record.module, **context, message=record.getMessage())
            stream.write(json.dumps(data, default=serializable, ensure_ascii=False) + '\n')
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        for stream in self.files.values():
            stream.close()
        self.files.clear()
        super().close()


def setup(directory=None):
    global _listener, _handler
    if _listener is not None:
        return
    q = queue.Queue()
    _handler = logging.handlers.QueueHandler(q)
    _handler.addFilter(ContextFilter())
    sink = SessionFiles(directory or config.ROOT / 'tmp' / 'logs', uuid4().hex)
    _listener = logging.handlers.QueueListener(q, sink)
    _listener.sink = sink
    LOG.setLevel(logging.INFO)
    LOG.addHandler(_handler)
    _listener.start()
    LOG.info('server.start')


def shutdown():
    global _listener, _handler
    if _listener is None:
        return
    LOG.info('server.stop')
    LOG.removeHandler(_handler)
    _listener.stop()
    _listener.sink.close()
    _listener = _handler = None


@contextmanager
def scope(session_id=None, session_uuid=None, **fields):
    context = {**CONTEXT.get(), 'operation_id': uuid4().hex, **fields}
    if session_id is not None:
        context.update(session_id=session_id, session_uuid=session_uuid or '-')
    token = CONTEXT.set(context)
    try:
        yield
    finally:
        CONTEXT.reset(token)


def event(name, **fields):
    # Payload fields are explicitly selected by callers; never serialize clients/headers/env.
    LOG.info('%s %s', name, json.dumps(fields, default=serializable, ensure_ascii=False), stacklevel=2)
