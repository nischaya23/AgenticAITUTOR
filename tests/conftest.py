import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from backend import config, db, main, utils


REAL_CALL_LLM = utils.call_llm

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(config, 'OBSERVER_LLM_ENABLED', False)
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread': False})
    event.listen(engine, 'connect', db.sqlite_setup)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setattr(main, 'SessionLocal', factory)
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    from model_fixtures import mock_llm
    from backend import assessment
    monkeypatch.setattr(utils, 'call_llm', mock_llm)
    async def mock_assess(problem, answer, context, reasoning=False):
        if reasoning:
            return dict(correct=False,score=.5,verified=False,reason='Provisional reasoning.',misconceptions=[])
        correct=(answer['text'] if isinstance(answer,dict) else answer).strip()==problem.get('expected_answer')
        return dict(correct=correct,score=float(correct),verified=True,reason='Test reference.',misconceptions=[])
    monkeypatch.setattr(assessment, 'evaluate_attempt', mock_assess)
    from backend import websearch
    async def offline_search(query, max_pages=None):
        raise websearch.WebSearchError('Web search is disabled in tests.')
    monkeypatch.setattr(websearch, 'search', offline_search)
    utils.METRICS.clear()
    with TestClient(main.app) as client:
        response = client.post('/api/auth/signup', json={'email': 'student@local.test', 'password': 'test-password'})
        assert response.status_code == 201, response.text
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
