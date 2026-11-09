"""Email/password accounts with revocable, opaque cookie sessions."""
import hashlib
import hmac
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import config, db, utils

router = APIRouter(prefix='/api/auth')
COOKIE = 'studyroom_session'
MAX_AGE = 7 * 24 * 60 * 60


class Credentials(utils.Schema):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address.')
        return value


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ':' + digest.hex()


def verify_password(password: str, stored: str | None) -> bool:
    # Match the cost of checking a valid account even when no account was found.
    encoded = stored or ('0' * 32 + ':' + '0' * 128)
    salt = encoded.split(':', 1)[0]
    return hmac.compare_digest(password_hash(password, salt), encoded) and stored is not None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_for_token(connection, token: str | None):
    if not token or len(token) > 128:
        return None
    return connection.scalar(select(db.User).join(db.Login, db.Login.user_id == db.User.id).where(
        db.Login.token_hash == token_hash(token), db.Login.expires_at > int(time.time())))


def set_login(connection, response: Response, user, old_token: str | None):
    if old_token:
        connection.execute(delete(db.Login).where(db.Login.token_hash == token_hash(old_token)))
    connection.execute(delete(db.Login).where(db.Login.expires_at <= int(time.time())))
    token = secrets.token_urlsafe(32)
    connection.add(db.Login(token_hash=token_hash(token), user_id=user.id,
                            expires_at=int(time.time()) + MAX_AGE))
    response.set_cookie(COOKIE, token, max_age=MAX_AGE, httponly=True,
                        secure=config.COOKIE_SECURE, samesite='strict', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'user': {'id': user.id, 'email': user.email}}


@router.post('/signup', status_code=201)
def signup(credentials: Credentials, request: Request, response: Response):
    hashed = password_hash(credentials.password)
    try:
        with db.SessionLocal.begin() as connection:
            user = db.User(email=credentials.email, password_hash=hashed)
            connection.add(user)
            connection.flush()
            return set_login(connection, response, user, request.cookies.get(COOKIE))
    except IntegrityError:
        raise HTTPException(409, 'An account with this email already exists.') from None


@router.post('/login')
def login(credentials: Credentials, request: Request, response: Response):
    with db.SessionLocal.begin() as connection:
        user = connection.scalar(select(db.User).where(db.User.email == credentials.email))
        if not verify_password(credentials.password, user.password_hash if user else None):
            raise HTTPException(401, 'Email or password is incorrect.')
        return set_login(connection, response, user, request.cookies.get(COOKIE))


@router.get('/me')
def me(request: Request):
    with db.SessionLocal() as connection:
        user = user_for_token(connection, request.cookies.get(COOKIE))
        if user is None:
            raise HTTPException(401, 'Please log in.')
        return {'user': {'id': user.id, 'email': user.email}}
