from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    url = Settings().database_url
    options = {"check_same_thread": False} if url.startswith("sqlite") else {"connect_timeout": 5}
    return create_engine(url, connect_args=options, pool_pre_ping=True, pool_timeout=5)


@lru_cache
def session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session():
    with session_factory()() as session:
        yield session
