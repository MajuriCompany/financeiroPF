"""
Parsers para importação em lote de fatura de cartão (CSV) e extrato de conta (OFX) — Sicredi.
Funções puras: recebem bytes/texto, devolvem list[dict]. Sem acesso a banco.
"""

import calendar
import csv
import io
import re
from collections import Counter
from datetime import datetime


# ── Fatura do cartão (CSV) ──────────────────────────────────────────────────

_SKIP_CARD_DESC = re.compile(r'pag\.?\s*fat|pagamento\s+recebido|pagamento\s+efetuado', re.I)


def normalize_text(raw: bytes) -> str:
    text = raw.decode('utf-8-sig', errors='replace')
    if 'Ã' in text or 'Â' in text:
        try:
            fixed = text.encode('latin1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            fixed = None
        if fixed is not None:
            mojibake_before = text.count('Ã') + text.count('Â')
            mojibake_after = fixed.count('Ã') + fixed.count('Â')
            if mojibake_after < mojibake_before:
                return fixed
    return text


def _parse_brl_amount(raw: str) -> float:
    s = raw.strip().replace('R$', '').strip()
    s = s.replace('.', '').replace(',', '.')
    return float(s)


def _parse_br_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), '%d/%m/%Y').date().isoformat()


def _find_invoice_month(lines: list[str], header_idx: int) -> tuple[int, int] | None:
    """Deriva o mês de referência da fatura a partir de 'Data de Vencimento' nos
    metadados (linhas antes do header). O vencimento cai no mês seguinte ao
    fechamento, então o mês da fatura é o mês anterior ao vencimento."""
    for line in lines[:header_idx]:
        cols = line.split(';')
        if len(cols) >= 2 and cols[0].strip() == 'Data de Vencimento':
            try:
                due = datetime.strptime(cols[1].strip(), '%d/%m/%Y').date()
            except ValueError:
                return None
            year, month = due.year, due.month - 1
            if month == 0:
                month, year = 12, year - 1
            return (year, month)
    return None


def _adjust_to_invoice_month(date_iso: str, invoice_month: tuple[int, int]) -> str:
    year, month = invoice_month
    d = datetime.strptime(date_iso, '%Y-%m-%d').date()
    if (d.year, d.month) == (year, month):
        return date_iso
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, min(d.day, last_day)).date().isoformat()


def parse_card_csv(text: str) -> list[dict]:
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        cols = line.split(';')
        if len(cols) >= 2 and cols[0].strip() == 'Data' and cols[1].strip().startswith('Descri'):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError('Cabeçalho da fatura não encontrado no CSV (esperado "Data;Descrição;...")')

    invoice_month = _find_invoice_month(lines, header_idx)

    body = '\n'.join(lines[header_idx + 1:])
    reader = csv.reader(io.StringIO(body), delimiter=';')

    rows = []
    for row in reader:
        if len(row) < 7:
            continue
        date_raw, desc_raw, parcela_raw, valor_raw, _dolar, _adicional, nome_raw = row[:7]
        date_raw = date_raw.strip()
        desc_raw = ' '.join(desc_raw.split())
        valor_raw = valor_raw.strip()
        if not date_raw or not desc_raw or not valor_raw:
            continue
        if _SKIP_CARD_DESC.search(desc_raw):
            continue
        try:
            amount = _parse_brl_amount(valor_raw)
            date_iso = _parse_br_date(date_raw)
        except ValueError:
            continue

        rows.append({
            'description': desc_raw,
            'original_description': desc_raw,
            'amount': round(amount, 2),
            'date': date_iso,
            'source_name': nome_raw.strip(),
            'parcela': parcela_raw.strip() or None,
        })

    if invoice_month is None and rows:
        year_months = [tuple(int(x) for x in r['date'].split('-')[:2]) for r in rows]
        invoice_month = Counter(year_months).most_common(1)[0][0]

    if invoice_month:
        for r in rows:
            r['date'] = _adjust_to_invoice_month(r['date'], invoice_month)

    return rows


# ── Extrato da conta (OFX) ──────────────────────────────────────────────────

_SKIP_OFX_MEMO = re.compile(
    r'PAGTO\s+FATURA|PAGAMENTO\s+FATURA|APLIC\.?\s*FINANC|APLICA[ÇC][ÃA]O\s+FINANCEIRA|RESGATE',
    re.I,
)
_STMTTRN_RE = re.compile(r'<STMTTRN>(.*?)</STMTTRN>', re.S)
_CPF_CNPJ_RE = re.compile(r'\d{11}|\d{14}')


def decode_ofx_bytes(raw: bytes) -> str:
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def _extract_field(block: str, tag: str) -> str:
    m = re.search(rf'<{tag}>([^\r\n<]*)', block)
    return m.group(1).strip() if m else ''


def _infer_payment_method(tipo: str, doc_code: str) -> str:
    tipo_u = tipo.upper()
    doc_u = doc_code.upper()
    if 'PIX' in tipo_u or doc_u.startswith('PIX') or doc_u.startswith('CX'):
        return 'PIX'
    if 'BOLETO' in tipo_u:
        return 'Boleto'
    if doc_u.startswith('TG') or 'PASSAGEM' in tipo_u or 'MENSALID' in tipo_u:
        return 'Débito Automático'
    if doc_u.startswith('CM') or 'COMPRA DEBITO' in tipo_u:
        return 'Cartão de Débito'
    return 'Débito'


def _parse_memo(memo: str) -> tuple[str, str]:
    memo = memo.strip()
    tipo, _, resto = memo.partition('-')
    tipo = tipo.strip()
    tokens = resto.split()
    doc_code = tokens[0] if tokens else ''
    remaining = tokens[1:]
    if remaining and _CPF_CNPJ_RE.fullmatch(remaining[0]):
        remaining = remaining[1:]
    description = ' '.join(remaining).strip() or tipo or memo
    return description, _infer_payment_method(tipo, doc_code)


def parse_account_ofx(text: str) -> list[dict]:
    rows = []
    for block in _STMTTRN_RE.findall(text):
        amt_raw = _extract_field(block, 'TRNAMT')
        dt_raw = _extract_field(block, 'DTPOSTED')
        memo = _extract_field(block, 'MEMO')
        fitid = _extract_field(block, 'FITID')
        if not amt_raw or not dt_raw:
            continue
        try:
            amount = float(amt_raw)
        except ValueError:
            continue
        if amount >= 0:
            continue
        if _SKIP_OFX_MEMO.search(memo):
            continue
        try:
            date_iso = datetime.strptime(dt_raw[:8], '%Y%m%d').date().isoformat()
        except ValueError:
            continue

        description, payment_method = _parse_memo(memo)
        rows.append({
            'description': description,
            'original_description': memo,
            'amount': round(abs(amount), 2),
            'date': date_iso,
            'payment_method': payment_method,
            'fitid': fitid or None,
        })
    return rows
