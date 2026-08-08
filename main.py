import csv
import io
import os
import uuid
from datetime import date as DateT, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import bcrypt as _bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import extract, func, text
from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Category, Group, Transaction, User
from importers import normalize_text, parse_card_csv, decode_ofx_bytes, parse_account_ofx

Base.metadata.create_all(bind=engine)


def _migrate_schema():
    """ALTER TABLE manual pra colunas novas em tabelas já existentes — o projeto não
    usa Alembic, então isso é dívida técnica deliberada. Idempotente e à prova de
    falha transitória de DDL (não deve derrubar o cold start)."""
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute(text("ALTER TABLE categories ADD COLUMN IF NOT EXISTS group_name VARCHAR"))
            else:
                cols = [row[1] for row in conn.execute(text("PRAGMA table_info(categories)"))]
                if "group_name" not in cols:
                    conn.execute(text("ALTER TABLE categories ADD COLUMN group_name VARCHAR"))
            conn.commit()
    except Exception:
        pass


_migrate_schema()

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

# ── Auth config ───────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-financas-pf-change-in-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 60


def _hash_pw(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()


def _verify_pw(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> str:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    username = payload.get("sub")
    if not username:
        raise JWTError("no sub")
    return username


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Finanças Pessoais")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# Auth middleware — protege todos os /api/* exceto /api/auth/login e /api/auth/register
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/register"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _PUBLIC_PATHS:
        return await call_next(request)

    # Aceita token no header ou como query param (para links de download)
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        token = request.query_params.get("token", "")

    if not token:
        return JSONResponse({"detail": "Não autenticado"}, status_code=401)
    try:
        _decode_token(token)
    except JWTError:
        return JSONResponse({"detail": "Token inválido"}, status_code=401)

    return await call_next(request)


# ── Auth endpoints ────────────────────────────────────────────────────────────


class AuthRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def auth_register(req: AuthRequest, db: Session = Depends(get_db)):
    if len(req.username.strip()) < 3:
        raise HTTPException(400, "Nome de usuário deve ter ao menos 3 caracteres")
    if len(req.password) < 4:
        raise HTTPException(400, "Senha deve ter ao menos 4 caracteres")
    if db.query(User).filter(User.username == req.username.strip()).first():
        raise HTTPException(400, "Usuário já existe")
    user = User(
        id=str(uuid.uuid4()),
        username=req.username.strip(),
        password_hash=_hash_pw(req.password),
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    return {"token": _create_token(user.username), "username": user.username}


@app.post("/api/auth/login")
async def auth_login(req: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username.strip()).first()
    if not user or not _verify_pw(req.password, user.password_hash):
        raise HTTPException(401, "Usuário ou senha inválidos")
    return {"token": _create_token(user.username), "username": user.username}


@app.get("/api/auth/me")
async def auth_me(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    username = _decode_token(token)
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(404, "Usuário não encontrado")
    return {"username": user.username}


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
    group_name: Optional[str] = None


class GroupCreate(BaseModel):
    name: str


class CategoryGroupUpdate(BaseModel):
    group_name: Optional[str] = None


class ImportRow(BaseModel):
    description: str
    amount: float
    date: DateT
    category: str
    responsible: Optional[str] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class ImportConfirmRequest(BaseModel):
    rows: List[ImportRow]


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
        result.append({"id": c.id, "name": c.name, "group_name": c.group_name, "transaction_count": count})
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


# ── Importação (fatura cartão / extrato conta) ─────────────────────────────────


@app.post("/api/import/card/preview")
async def import_card_preview(file: UploadFile):
    raw = await file.read()
    try:
        rows = parse_card_csv(normalize_text(raw))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows}


@app.post("/api/import/account/preview")
async def import_account_preview(file: UploadFile):
    raw = await file.read()
    try:
        rows = parse_account_ofx(decode_ofx_bytes(raw))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows}


@app.post("/api/import/confirm", status_code=201)
async def import_confirm(body: ImportConfirmRequest, db: Session = Depends(get_db)):
    existing_categories = {c.name for c in db.query(Category).all()}
    imported = 0

    for row in body.rows:
        if row.category not in existing_categories:
            db.add(Category(id=str(uuid.uuid4()), name=row.category))
            existing_categories.add(row.category)

        t = Transaction(
            id=str(uuid.uuid4()),
            description=row.description,
            amount=row.amount,
            type="expense",
            category=row.category,
            payment_method=row.payment_method,
            responsible=row.responsible,
            notes=row.notes,
            date=row.date,
            created_at=datetime.utcnow(),
            amount_invalid=False,
        )
        db.add(t)
        imported += 1

    db.commit()
    return {"imported": imported}


# ── Groups ───────────────────────────────────────────────────────────────────


@app.get("/api/groups")
async def list_groups(db: Session = Depends(get_db)):
    groups = db.query(Group).order_by(Group.name).all()
    result = []
    for g in groups:
        count = db.query(Category).filter(Category.group_name == g.name).count()
        result.append({"id": g.id, "name": g.name, "category_count": count})
    return result


@app.post("/api/groups", status_code=201)
async def create_group(body: GroupCreate, db: Session = Depends(get_db)):
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(400, "Grupo já existe")
    g = Group(id=str(uuid.uuid4()), name=body.name)
    db.add(g)
    db.commit()
    db.refresh(g)
    return {"id": g.id, "name": g.name, "category_count": 0}


@app.put("/api/groups/{group_id}")
async def update_group(group_id: str, body: GroupCreate, db: Session = Depends(get_db)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(404, "Grupo não encontrado")

    if db.query(Group).filter(Group.name == body.name, Group.id != group_id).first():
        raise HTTPException(400, "Já existe um grupo com esse nome")

    old_name = g.name
    g.name = body.name
    db.query(Category).filter(Category.group_name == old_name).update({"group_name": body.name})
    db.commit()
    count = db.query(Category).filter(Category.group_name == body.name).count()
    return {"id": g.id, "name": g.name, "category_count": count}


@app.delete("/api/groups/{group_id}", status_code=204)
async def delete_group(group_id: str, db: Session = Depends(get_db)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(404, "Grupo não encontrado")

    db.query(Category).filter(Category.group_name == g.name).update({"group_name": None})
    db.delete(g)
    db.commit()


@app.put("/api/categories/{cat_id}/group")
async def set_category_group(cat_id: str, body: CategoryGroupUpdate, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(404, "Categoria não encontrada")

    if body.group_name is not None:
        if not db.query(Group).filter(Group.name == body.group_name).first():
            raise HTTPException(400, f"Grupo '{body.group_name}' não existe")

    cat.group_name = body.group_name
    db.commit()
    count = db.query(Transaction).filter(Transaction.category == cat.name).count()
    return {"id": cat.id, "name": cat.name, "group_name": cat.group_name, "transaction_count": count}


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
        return {"months": [], "year": year, "categories": [], "groups": [], "total_expense": 0.0, "total_income": 0.0}

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
                        "id": t.id,
                        "description": t.description,
                        "amount": round(t.amount, 2),
                        "date": t.date.isoformat() if t.date else None,
                        "type": t.type,
                        "category": t.category,
                        "responsible": t.responsible,
                        "payment_method": t.payment_method,
                        "notes": t.notes,
                        "amount_invalid": t.amount_invalid,
                    }
                    for t in sorted(txs, key=lambda t: t.date or DateT.min, reverse=True)
                ],
            }
        )

    cat_to_group = {c.name: c.group_name for c in db.query(Category).all() if c.group_name}
    group_txs: dict = {}
    for t in expense_txs:
        group_txs.setdefault(cat_to_group.get(t.category, "Sem Grupo"), []).append(t)

    groups = []
    for group_name, txs in sorted(group_txs.items(), key=lambda x: sum(t.amount for t in x[1]), reverse=True):
        total = sum(t.amount for t in txs)
        groups.append(
            {
                "name": group_name,
                "total": round(total, 2),
                "count": len(txs),
                "percentage": round((total / total_expense * 100) if total_expense else 0, 1),
            }
        )

    return {
        "months": month_list,
        "year": year,
        "categories": categories,
        "groups": groups,
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
