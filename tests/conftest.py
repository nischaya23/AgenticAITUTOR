import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from backend import config, db, main, utils


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread': False})
    event.listen(engine, 'connect', db.sqlite_setup)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setattr(main, 'SessionLocal', factory)
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(config, 'DEMO_MODE', True)
    utils.METRICS.clear()
    with TestClient(main.app) as client:
        yield client, factory, engine
    engine.dispose()


def receive_until(socket, kind):
    for _ in range(20):
        message = socket.receive_json()
        if message['type'] == 'error':
            raise AssertionError(message['message'])
        if message['type'] == kind:
            return message
    raise AssertionError(f'No {kind} response')
