import csv
import io
import os
import uuid
from datetime import date as DateT, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Category, Transaction

Base.metadata.create_all(bind=engine)

BASE_DIR = Path(__file__).resolve().parent


def _seed_if_empty():
    """Popula o banco com seed_data.json se estiver vazio (ex: Vercel cold start)."""
    seed_file = BASE_DIR / "seed_data.json"
    if not seed_file.exists():
        return
    db = SessionLocal()
    try:
        if db.query(Transaction).count() > 0:
            return
        import json
        data = json.loads(seed_file.read_text(encoding="utf-8"))
        for c in data.get("categories", []):
            if not db.query(Category).filter(Category.id == c["id"]).first():
                db.add(Category(id=c["id"], name=c["name"]))
        for t in data.get("transactions", []):
            if db.query(Transaction).filter(Transaction.id == t["id"]).first():
                continue
            from datetime import date as date_type, datetime as dt_type
            tx_date = date_type.fromisoformat(t["date"]) if t.get("date") else None
            tx_created = dt_type.fromisoformat(t["created_at"]) if t.get("created_at") else None
            db.add(Transaction(
                id=t["id"], description=t["description"], amount=t["amount"],
                type=t["type"], category=t["category"],
                payment_method=t.get("payment_method"), responsible=t.get("responsible"),
                notes=t.get("notes"), date=tx_date, created_at=tx_created,
                amount_invalid=t.get("amount_invalid", False),
            ))
        db.commit()
    finally:
        db.close()


_seed_if_empty()

app = FastAPI(title="Finanças Pessoais")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/api/health")
def health():
    from database import SQLALCHEMY_DATABASE_URL
    db_type = SQLALCHEMY_DATABASE_URL.split("://")[0]
    return {
        "db": db_type,
        "url_prefix": SQLALCHEMY_DATABASE_URL[:30] + "...",
        "env": {
            "DATABASE_URL_UNPOOLED": bool(os.environ.get("DATABASE_URL_UNPOOLED")),
            "STORAGE_URL_UNPOOLED": bool(os.environ.get("STORAGE_URL_UNPOOLED")),
            "DATABASE_URL": bool(os.environ.get("DATABASE_URL")),
            "STORAGE_URL": bool(os.environ.get("STORAGE_URL")),
        }
    }


# ── Schemas ──────────────────────────────────────────────────────────────────


class TransactionCreate(BaseModel):
    description: str
    amount: Optional[float] = None
    type: str
    category: str
    payment_method: Optional[str] = None
    responsible: Optional[str] = None
    notes: Optional[str] = None
    date: DateT
    amount_invalid: bool = False


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    category: Optional[str] = None
    payment_method: Optional[str] = None
    responsible: Optional[str] = None
    notes: Optional[str] = None
    date: Optional[DateT] = None
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
    date: Optional[DateT]
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
        extract("month", Transaction.date) == month,
        extract("year", Transaction.date) == year,
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
    date_from: Optional[DateT] = None,
    date_to: Optional[DateT] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    responsible: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    invalid_only: bool = False,
    sort_by: str = "date",
    sort_order: str = "desc",
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)

    if date_from and date_to:
        q = q.filter(Transaction.date >= date_from, Transaction.date <= date_to)
    elif month and year:
        q = _month_filter(q, month, year)
    elif year:
        q = q.filter(extract("year", Transaction.date) == year)

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
    sort_col = Transaction.amount if sort_by == "amount" else Transaction.date
    if sort_order == "asc":
        q = q.order_by(sort_col.asc().nulls_last())
    else:
        q = q.order_by(sort_col.desc().nulls_first())
    items = q.offset((page - 1) * per_page).limit(per_page).all()

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
    total_investment = sum(t.amount for t in curr if t.type == "investment")
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
        "total_investment": round(total_investment, 2),
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


@app.get("/api/report/multi")
async def get_report_multi(months: str, year: int, db: Session = Depends(get_db)):
    month_list = [int(m.strip()) for m in months.split(",") if m.strip().isdigit()]
    if not month_list:
        return {"months": [], "year": year, "categories": [], "total_expense": 0.0, "total_income": 0.0}

    expense_txs = []
    income_total = 0.0

    for m in month_list:
        txs = (
            _month_filter(db.query(Transaction), m, year)
            .filter(
                Transaction.type == "expense",
                Transaction.amount_invalid == False,
                Transaction.amount.isnot(None),
            )
            .all()
        )
        expense_txs.extend(txs)

        income = (
            _month_filter(db.query(Transaction), m, year)
            .filter(
                Transaction.type == "income",
                Transaction.amount_invalid == False,
                Transaction.amount.isnot(None),
            )
            .all()
        )
        income_total += sum(t.amount for t in income)

    total_expense = sum(t.amount for t in expense_txs)

    cat_txs: dict = {}
    for t in expense_txs:
        cat_txs.setdefault(t.category, []).append(t)

    categories = []
    for cat, txs in sorted(cat_txs.items(), key=lambda x: sum(t.amount for t in x[1]), reverse=True):
        total = sum(t.amount for t in txs)
        categories.append(
            {
                "name": cat,
                "total": round(total, 2),
                "count": len(txs),
                "percentage": round((total / total_expense * 100) if total_expense else 0, 1),
                "transactions": [
                    {
                        "description": t.description,
                        "amount": round(t.amount, 2),
                        "date": t.date.isoformat() if t.date else None,
                    }
                    for t in sorted(txs, key=lambda t: t.date or DateT.min, reverse=True)
                ],
            }
        )

    return {
        "months": month_list,
        "year": year,
        "categories": categories,
        "total_expense": round(total_expense, 2),
        "total_income": round(income_total, 2),
    }


# ── Export ────────────────────────────────────────────────────────────────────


@app.get("/api/export")
async def export_csv(
    month: Optional[int] = None,
    year: Optional[int] = None,
    date_from: Optional[DateT] = None,
    date_to: Optional[DateT] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if date_from and date_to:
        q = q.filter(Transaction.date >= date_from, Transaction.date <= date_to)
    elif month and year:
        q = _month_filter(q, month, year)
    elif year:
        q = q.filter(extract("year", Transaction.date) == year)

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


# ── Evolution ──────────────────────────────────────────────────────────────────


@app.get("/api/evolution")
async def get_evolution(months: int = 6, db: Session = Depends(get_db)):
    from datetime import date as date_cls
    today = date_cls.today()
    ABBR = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
            "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    result = []
    for i in range(months - 1, -1, -1):
        idx = today.year * 12 + (today.month - 1) - i
        y, m = idx // 12, idx % 12 + 1
        txs = (
            _month_filter(db.query(Transaction), m, y)
            .filter(Transaction.amount_invalid == False, Transaction.amount.isnot(None))
            .all()
        )
        result.append({
            "month": m, "year": y,
            "label": f"{ABBR[m]}/{str(y)[2:]}",
            "income": round(sum(t.amount for t in txs if t.type == "income"), 2),
            "expense": round(sum(t.amount for t in txs if t.type == "expense"), 2),
            "investment": round(sum(t.amount for t in txs if t.type == "investment"), 2),
        })
    return result
