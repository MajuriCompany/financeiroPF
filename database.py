import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Prefere conexão direta (sem PgBouncer) para compatibilidade com SQLAlchemy.
# Neon cria DATABASE_URL (pooled) e DATABASE_URL_UNPOOLED (direto).
_pg = (
    os.environ.get("DATABASE_URL_UNPOOLED")       # Neon direto — preferido
    or os.environ.get("POSTGRES_URL_NON_POOLING") # Neon / Supabase alternativo
    or os.environ.get("DATABASE_URL")             # Pooled (fallback)
    or os.environ.get("STORAGE_URL")              # Prefixo custom Vercel
    or os.environ.get("POSTGRES_URL")             # Outros provedores
)

if _pg:
    if _pg.startswith("postgres://"):
        _pg = _pg.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URL = _pg
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
else:
    # Sem variável de banco cloud — usa SQLite
    # /tmp é o único diretório gravável no Vercel; localmente usa arquivo local
    _sqlite_path = "/tmp/financas.db" if os.path.exists("/tmp") else "./financas.db"
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{_sqlite_path}"
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
