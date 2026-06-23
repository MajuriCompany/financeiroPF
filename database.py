import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# No Vercel o disco é efêmero — usa /tmp; localmente usa ./financas.db
if os.environ.get("VERCEL"):
    SQLALCHEMY_DATABASE_URL = "sqlite:////tmp/financas.db"
else:
    SQLALCHEMY_DATABASE_URL = "sqlite:///./financas.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
