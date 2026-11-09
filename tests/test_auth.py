import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from starlette.websockets import WebSocketDisconnect

from backend import auth, db, main, orchestrator


def test_login_logout_and_isolation(app_env):
    alice, factory, _ = app_env
    sid = alice.post('/api/sessions').json()['id']
    material = alice.post('/api/materials', files={'file': (
        'private.md', b'# Private lesson\nPrivate course content.', 'text/markdown')}).json()
    outsider = TestClient(main.app)
    try:
        assert outsider.get('/api/student').status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with outsider.websocket_connect(f'/ws/{sid}'):
                pass
        assert outsider.post('/api/auth/signup', json={
            'email': ' BOB@example.com ', 'password': 'different-password'}).status_code == 201
        assert outsider.get('/api/auth/me').json()['user']['email'] == 'bob@example.com'
        assert outsider.get(f'/api/sessions/{sid}').status_code == 404
        assert outsider.get(f'/api/sessions/+{sid}').status_code == 404
        assert outsider.post(f'/api/sessions/{sid}/skip', json={'problem_id': 'x'}).status_code == 404
        assert outsider.get('/api/sessions').json()['sessions'] == []
        assert outsider.get('/api/materials').json() == []
        assert outsider.get('/api/concepts').json()['nodes'] == []
        own_sid = outsider.post('/api/sessions').json()['id']
        assert outsider.post(f'/api/sessions/{own_sid}/study',
            json={'material_id': material['id']}).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with outsider.websocket_connect(f'/ws/{sid}'):
                pass
        with factory() as connection:
            context = orchestrator.context_for(connection, own_sid, 'Private lesson')
            assert context['knowledge'] == []
        assert alice.get('/api/materials').json()[0]['id'] == material['id']
        assert alice.get('/api/sessions').json()['sessions'][0]['id'] == sid
        assert outsider.post('/api/auth/logout').status_code == 200
        assert outsider.get('/api/auth/me').status_code == 401
        assert outsider.post('/api/auth/login', json={
            'email': 'bob@example.com', 'password': 'wrong-password'}).status_code == 401
        assert outsider.post('/api/auth/login', json={
            'email': 'BOB@example.com', 'password': 'different-password'}).status_code == 200
        assert outsider.post('/api/auth/signup', json={
            'email': 'bob@example.com', 'password': 'different-password'}).status_code == 409
        assert outsider.post('/api/sessions', headers={'Origin': 'http://other.example'}).status_code == 403
    finally:
        outsider.close()


def test_revocation_expiry_and_password_storage(app_env):
    client, factory, _ = app_env
    token = client.cookies.get(auth.COOKIE)
    with factory.begin() as connection:
        user = connection.scalar(select(db.User))
        assert user.password_hash != 'test-password'
        assert auth.verify_password('test-password', user.password_hash)
        login = connection.get(db.Login, auth.token_hash(token))
        assert login is not None and login.token_hash != token
        login.expires_at = int(time.time()) - 1
    assert client.get('/api/student').status_code == 401
    assert client.post('/api/auth/login', json={
        'email': 'student@local.test', 'password': 'test-password'}).status_code == 200
    token = client.cookies.get(auth.COOKIE)
    sid = client.post('/api/sessions').json()['id']
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.receive_json()
        assert client.post('/api/auth/logout').status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    client.cookies.set(auth.COOKIE, token)
    assert client.get('/api/student').status_code == 401


def test_existing_database_migrates_without_claiming_anonymous_data(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "old.db"}')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL)')
        connection.exec_driver_sql("INSERT INTO users VALUES (1, 'student@local.test')")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()
    assert 'password_hash' in {column['name'] for column in inspect(engine).get_columns('users')}
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT email, password_hash FROM users').one() == ('student@local.test', None)
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM logins').scalar() == 0
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM materials').scalar() == 0
    engine.dispose()
