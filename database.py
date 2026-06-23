import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Tenta todas as variáveis que Supabase/Neon/Vercel Postgres podem criar.
# Preferência para URLs diretas (sem PgBouncer) para evitar conflito com SQLAlchemy.
_pg = (
    os.environ.get("POSTGRES_URL_NON_POOLING")  # Neon / Supabase direto (sem pooler)
    or os.environ.get("DATABASE_URL")           # Prefixo DATABASE (recomendado)
    or os.environ.get("STORAGE_URL")            # Prefixo padrão Supabase Vercel
    or os.environ.get("POSTGRES_PRISMA_URL")    # Neon Prisma
    or os.environ.get("POSTGRES_URL")           # Neon pooled
)

if _pg:
    if _pg.startswith("postgres://"):
        _pg = _pg.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URL = _pg
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
else:
    # Desenvolvimento local — SQLite
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
