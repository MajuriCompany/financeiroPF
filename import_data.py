"""
Script de importação do Excel para SQLite.
Execute uma única vez: python import_data.py
É idempotente — rodar duas vezes não duplica dados.
"""

import sys
from datetime import datetime, timedelta, date as date_type

import pandas as pd
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import Base, Category, Transaction

EXCEL_FILE = "transacoes_2026-06-23.xlsx"
SHEET_NAME = "transacoes_2026-06-23"
AMOUNT_INVALID_THRESHOLD = 10000


def excel_serial_to_date(val) -> date_type | None:
    """Converte serial do Excel para date. Aceita serial, datetime ou string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (datetime,)):
        return val.date()
    if isinstance(val, date_type):
        return val
    try:
        serial = float(val)
        return (datetime(1899, 12, 30) + timedelta(days=int(serial))).date()
    except (TypeError, ValueError):
        try:
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def parse_amount(val) -> tuple[float | None, bool]:
    """Retorna (amount, amount_invalid). Valores > threshold são inválidos."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None, True
    try:
        amount = float(val)
        if amount > AMOUNT_INVALID_THRESHOLD:
            return None, True
        return amount, False
    except (TypeError, ValueError):
        return None, True


def parse_created_at(val) -> datetime | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    Base.metadata.create_all(bind=engine)

    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, dtype=str)
    except FileNotFoundError:
        print(f"ERRO: Arquivo '{EXCEL_FILE}' não encontrado.")
        print("Coloque o arquivo Excel na mesma pasta que este script e tente novamente.")
        sys.exit(1)
    except Exception as e:
        print(f"ERRO ao ler o Excel: {e}")
        sys.exit(1)

    print(f"Arquivo lido: {len(df)} linhas encontradas.")

    db: Session = SessionLocal()
    try:
        imported = 0
        skipped = 0
        invalid_amount = 0

        category_names: set[str] = set()

        for _, row in df.iterrows():
            tx_id = str(row.get("id", "")).strip()
            if not tx_id:
                continue

            # Skip if already exists (idempotência)
            if db.query(Transaction).filter(Transaction.id == tx_id).first():
                skipped += 1
                continue

            description = str(row.get("description", "")).strip() or "Sem descrição"

            # Amount
            amount_raw = row.get("amount")
            amount, is_invalid = parse_amount(amount_raw)

            tx_type = str(row.get("type", "expense")).strip()
            category = str(row.get("category", "Outros")).strip() or "Outros"
            payment_method = str(row.get("payment_method", "")).strip() or None
            responsible = str(row.get("responsible", "")).strip() or None
            notes = str(row.get("notes", "")).strip() or None
            if notes in ("nan", "None", ""):
                notes = None

            # Date — pode ser serial numérico ou string
            date_raw = row.get("date")
            try:
                date_float = float(date_raw)
                tx_date = excel_serial_to_date(date_float)
            except (TypeError, ValueError):
                tx_date = excel_serial_to_date(date_raw)

            created_at = parse_created_at(row.get("created_at"))

            category_names.add(category)

            t = Transaction(
                id=tx_id,
                description=description,
                amount=amount,
                type=tx_type,
                category=category,
                payment_method=payment_method,
                responsible=responsible,
                notes=notes,
                date=tx_date,
                created_at=created_at,
                amount_invalid=is_invalid,
            )
            db.add(t)

            if is_invalid:
                invalid_amount += 1
            imported += 1

        # Ensure all categories exist
        for name in category_names:
            if not db.query(Category).filter(Category.name == name).first():
                import uuid
                db.add(Category(id=str(uuid.uuid4()), name=name))

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"ERRO durante a importação: {e}")
        raise
    finally:
        db.close()

    print(f"\nImportacao concluida!")
    print(f"  {imported} transacoes importadas")
    print(f"  {skipped} transacoes ignoradas (ja existiam)")
    print(f"  {invalid_amount} transacoes com valor invalido (amount_invalid=True)")
    if invalid_amount:
        print(f"\n  ATENCAO: Acesse o sistema e filtre por 'Valor Invalido' para corrigir manualmente.")


if __name__ == "__main__":
    main()
