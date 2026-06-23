import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Vercel Postgres (Neon) sets POSTGRES_URL_NON_POOLING (preferred for DDL)
# Falls back to POSTGRES_URL, then DATABASE_URL, then local SQLite
_pg = (
    os.environ.get("POSTGRES_URL_NON_POOLING")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("DATABASE_URL")
)

if _pg:
    # SQLAlchemy requires "postgresql://" not "postgres://"
    if _pg.startswith("postgres://"):
        _pg = _pg.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URL = _pg
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
else:
    SQLALCHEMY_DATABASE_URL = "sqlite:///./financas.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
