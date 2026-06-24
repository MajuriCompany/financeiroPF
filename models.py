import uuid
from sqlalchemy import Column, String, Float, Boolean, Date, DateTime, Text
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=True)


class Category(Base):
    __tablename__ = "categories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=True)
    type = Column(String, nullable=False)
    category = Column(String, nullable=False)
    payment_method = Column(String, nullable=True)
    responsible = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    date = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=True)
    amount_invalid = Column(Boolean, default=False, nullable=False)
