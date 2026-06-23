import csv
import io
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Category, Transaction

Base.metadata.create_all(bind=engine)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Finanças Pessoais")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ── Schemas ──────────────────────────────────────────────────────────────────


class TransactionCreate(BaseModel):
    description: str
    amount: Optional[float] = None
    type: str
    category: str
    payment_method: Optional[str] = None
    responsible: Optional[str] = None
    notes: Optional[str] = None
    date: date
    amount_invalid: bool = False


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    category: Optional[str] = None
    payment_method: Optional[str] = None
    responsible: Optional[str] = None
    notes: Optional[str] = None
    date: Optional[date] = None
    amount_invalid: Optional[bool] = None


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    description: str
    amount: Optional[float]
    type: str
    category: str
    payment_method: Optional[str]
    responsible: Optional[str]
    notes: Optional[str]
    date: Optional[date]
    created_at: Optional[datetime]
    amount_invalid: bool


class CategoryCreate(BaseModel):
    name: str


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _month_filter(query, month: int, year: int):
    return query.filter(
        func.strftime("%m", Transaction.date) == f"{month:02d}",
        func.strftime("%Y", Transaction.date) == str(year),
    )


def _tx_dict(t: Transaction) -> dict:
    return {
        "id": t.id,
        "description": t.description,
        "amount": t.amount,
        "type": t.type,
        "category": t.category,
        "payment_method": t.payment_method,
        "responsible": t.responsible,
        "notes": t.notes,
        "date": t.date.isoformat() if t.date else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "amount_invalid": t.amount_invalid,
    }


# ── Root ──────────────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


# ── Transactions ──────────────────────────────────────────────────────────────


@app.get("/api/transactions")
async def list_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    responsible: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    invalid_only: bool = False,
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)

    if month and year:
        q = _month_filter(q, month, year)
    elif year:
        q = q.filter(func.strftime("%Y", Transaction.date) == str(year))

    if type:
        q = q.filter(Transaction.type == type)
    if category:
        q = q.filter(Transaction.category == category)
    if responsible:
        q = q.filter(Transaction.responsible == responsible)
    if payment_method:
        q = q.filter(Transaction.payment_method == payment_method)
    if search:
        q = q.filter(Transaction.description.ilike(f"%{search}%"))
    if invalid_only:
        q = q.filter(Transaction.amount_invalid == True)

    total = q.count()
    items = (
        q.order_by(Transaction.date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "items": [_tx_dict(t) for t in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


@app.post("/api/transactions", status_code=201)
async def create_transaction(body: TransactionCreate, db: Session = Depends(get_db)):
    # Ensure category exists
    cat = db.query(Category).filter(Category.name == body.category).first()
    if not cat:
        db.add(Category(id=str(uuid.uuid4()), name=body.category))

    t = Transaction(
        id=str(uuid.uuid4()),
        description=body.description,
        amount=body.amount,
        type=body.type,
        category=body.category,
        payment_method=body.payment_method,
        responsible=body.responsible,
        notes=body.notes,
        date=body.date,
        created_at=datetime.utcnow(),
        amount_invalid=body.amount_invalid,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tx_dict(t)


@app.put("/api/transactions/{tx_id}")
async def update_transaction(
    tx_id: str, body: TransactionUpdate, db: Session = Depends(get_db)
):
    t = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not t:
        raise HTTPException(404, "Transação não encontrada")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(t, field, value)

    # If a valid amount was provided, auto-clear the invalid flag
    if body.amount is not None and body.amount_invalid is None:
        t.amount_invalid = False

    db.commit()
    db.refresh(t)
    return _tx_dict(t)


@app.delete("/api/transactions/{tx_id}", status_code=204)
async def delete_transaction(tx_id: str, db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not t:
        raise HTTPException(404, "Transação não encontrada")
    db.delete(t)
    db.commit()


# ── Categories ────────────────────────────────────────────────────────────────


@app.get("/api/categories")
async def list_categories(db: Session = Depends(get_db)):
    cats = db.query(Category).order_by(Category.name).all()
    result = []
    for c in cats:
        count = db.query(Transaction).filter(Transaction.category == c.name).count()
        result.append({"id": c.id, "name": c.name, "transaction_count": count})
    return result


@app.post("/api/categories", status_code=201)
async def create_category(body: CategoryCreate, db: Session = Depends(get_db)):
    existing = db.query(Category).filter(Category.name == body.name).first()
    if existing:
        raise HTTPException(400, "Categoria já existe")
    cat = Category(id=str(uuid.uuid4()), name=body.name)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "transaction_count": 0}


@app.put("/api/categories/{cat_id}")
async def update_category(
    cat_id: str, body: CategoryCreate, db: Session = Depends(get_db)
):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(404, "Categoria não encontrada")

    if db.query(Category).filter(Category.name == body.name, Category.id != cat_id).first():
        raise HTTPException(400, "Já existe uma categoria com esse nome")

    old_name = cat.name
    cat.name = body.name
    db.query(Transaction).filter(Transaction.category == old_name).update(
        {"category": body.name}
    )
    db.commit()
    count = db.query(Transaction).filter(Transaction.category == body.name).count()
    return {"id": cat.id, "name": cat.name, "transaction_count": count}


@app.delete("/api/categories/{cat_id}", status_code=204)
async def delete_category(
    cat_id: str,
    reassign_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(404, "Categoria não encontrada")

    count = db.query(Transaction).filter(Transaction.category == cat.name).count()
    if count > 0:
        if not reassign_to:
            raise HTTPException(
                400,
                f"Categoria possui {count} transações. Forneça 'reassign_to' para reatribuir.",
            )
        target = db.query(Category).filter(Category.name == reassign_to).first()
        if not target:
            raise HTTPException(400, f"Categoria destino '{reassign_to}' não existe")
        db.query(Transaction).filter(Transaction.category == cat.name).update(
            {"category": reassign_to}
        )

    db.delete(cat)
    db.commit()


# ── Summary ───────────────────────────────────────────────────────────────────


@app.get("/api/summary")
async def get_summary(month: int, year: int, db: Session = Depends(get_db)):
    def valid_txs(m, y):
        return (
            _month_filter(db.query(Transaction), m, y)
            .filter(Transaction.amount_invalid == False, Transaction.amount.isnot(None))
            .all()
        )

    curr = valid_txs(month, year)
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    prev = valid_txs(prev_m, prev_y)

    total_income = sum(t.amount for t in curr if t.type == "income")
    total_expense = sum(t.amount for t in curr if t.type == "expense")
    prev_expense = sum(t.amount for t in prev if t.type == "expense")
    prev_income = sum(t.amount for t in prev if t.type == "income")

    by_resp: dict = {}
    for t in curr:
        if t.type == "expense":
            by_resp[t.responsible] = by_resp.get(t.responsible, 0) + t.amount

    by_cat: dict = {}
    for t in curr:
        if t.type == "expense":
            by_cat[t.category] = by_cat.get(t.category, 0) + t.amount

    by_cat_list = sorted(
        [{"name": k, "total": round(v, 2)} for k, v in by_cat.items()],
        key=lambda x: x["total"],
        reverse=True,
    )

    invalid_count = db.query(Transaction).filter(Transaction.amount_invalid == True).count()

    recent_q = _month_filter(db.query(Transaction), month, year)
    recent = recent_q.order_by(Transaction.date.desc()).limit(8).all()

    return {
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "balance": round(total_income - total_expense, 2),
        "by_responsible": {k: round(v, 2) for k, v in by_resp.items()},
        "by_category": by_cat_list,
        "invalid_count": invalid_count,
        "transaction_count": len(curr),
        "prev_month_expense": round(prev_expense, 2),
        "prev_month_income": round(prev_income, 2),
        "recent": [_tx_dict(t) for t in recent],
    }


# ── Report ────────────────────────────────────────────────────────────────────


@app.get("/api/report")
async def get_report(month: int, year: int, db: Session = Depends(get_db)):
    def expenses(m, y):
        return (
            _month_filter(db.query(Transaction), m, y)
            .filter(
                Transaction.type == "expense",
                Transaction.amount_invalid == False,
                Transaction.amount.isnot(None),
            )
            .all()
        )

    curr = expenses(month, year)
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    prev = expenses(prev_m, prev_y)

    total_expense = sum(t.amount for t in curr)
    total_income = sum(
        t.amount
        for t in _month_filter(db.query(Transaction), month, year)
        .filter(
            Transaction.type == "income",
            Transaction.amount_invalid == False,
            Transaction.amount.isnot(None),
        )
        .all()
    )

    cat_curr: dict = {}
    cat_count: dict = {}
    for t in curr:
        cat_curr[t.category] = cat_curr.get(t.category, 0) + t.amount
        cat_count[t.category] = cat_count.get(t.category, 0) + 1

    cat_prev: dict = {}
    for t in prev:
        cat_prev[t.category] = cat_prev.get(t.category, 0) + t.amount

    categories = []
    for cat, total in sorted(cat_curr.items(), key=lambda x: x[1], reverse=True):
        prev_total = cat_prev.get(cat, 0)
        categories.append(
            {
                "name": cat,
                "total": round(total, 2),
                "count": cat_count[cat],
                "percentage": round((total / total_expense * 100) if total_expense else 0, 1),
                "prev_total": round(prev_total, 2),
                "delta": round(total - prev_total, 2),
            }
        )

    invalid_count = db.query(Transaction).filter(Transaction.amount_invalid == True).count()

    return {
        "month": month,
        "year": year,
        "categories": categories,
        "total_expense": round(total_expense, 2),
        "total_income": round(total_income, 2),
        "invalid_count": invalid_count,
    }


# ── Export ────────────────────────────────────────────────────────────────────


@app.get("/api/export")
async def export_csv(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if month and year:
        q = _month_filter(q, month, year)
    elif year:
        q = q.filter(func.strftime("%Y", Transaction.date) == str(year))

    txs = q.order_by(Transaction.date.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Data", "Descrição", "Valor", "Tipo", "Categoria", "Método", "Responsável", "Observações", "Valor Inválido"]
    )
    for t in txs:
        writer.writerow(
            [
                t.date.isoformat() if t.date else "",
                t.description,
                t.amount if t.amount is not None else "",
                "Despesa" if t.type == "expense" else "Receita",
                t.category,
                t.payment_method or "",
                t.responsible or "",
                t.notes or "",
                "Sim" if t.amount_invalid else "Não",
            ]
        )

    filename = f"transacoes_{year or 'todos'}_{month or 'todos'}.csv"
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
