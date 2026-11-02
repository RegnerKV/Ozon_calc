from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import sqlite3

ZERO = Decimal('0')


def money(value) -> Decimal:
    if value is None or str(value).strip().lower() in ('',):
        return ZERO
    s = str(value).replace('\u00a0', '').replace('\u202f', '').replace(' ', '').replace('₽', '').replace(',', '.')
    try:
        result = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f'Не удалось прочитать число: {value}') from exc
    if not result.is_finite():
        raise ValueError('Число должно быть конечным')
    return result


def rub(value) -> Decimal:
    return money(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def data_dir() -> Path:
    root = os.environ.get('OZON_PROFIT_DATA_DIR')
    path = Path(root) if root else Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'OzonProfit'
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(path=None):
    db = sqlite3.connect(path or data_dir() / 'profit.db')
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS costs(sku TEXT NOT NULL REFERENCES products(sku), since TEXT NOT NULL,
      amount TEXT NOT NULL, estimated INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', PRIMARY KEY(sku,since));
    CREATE TABLE IF NOT EXISTS imports(digest TEXT PRIMARY KEY, name TEXT, imported_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, day TEXT NOT NULL, order_no TEXT NOT NULL,
      sku TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, amount TEXT NOT NULL, qty TEXT NOT NULL,
      source TEXT NOT NULL REFERENCES imports(digest));
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    return db


def set_setting(db, key, value):
    with db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))


def get_setting(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def save_cost(db, sku, name, since, amount, estimated=False, note=''):
    sku = str(sku).strip()
    if not sku or not name.strip():
        raise ValueError('Укажите артикул и название')
    date.fromisoformat(str(since))
    amount = money(amount)
    if amount < 0:
        raise ValueError('Себестоимость не может быть отрицательной')
    with db:
        db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET name=excluded.name', (sku, name.strip()))
        db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)', (sku, str(since), str(amount), int(estimated), note))


KINDS = {
    'sale': 'Продажа (валовая выручка)',
    'return': 'Возврат выручки',
    'commission': 'Комиссия',
    'logistics': 'Логистика',
    'advertising': 'Реклама',
    'service': 'Прочие услуги',
    'compensation': 'Компенсация',
    'points': 'Баллы за скидки / возмещение',
    'points_reversal': 'Сторно баллов за скидки',
    'partner': 'Программы партнёров',
    'service_refund': 'Возврат удержаний / услуг Ozon',
    'advertising_refund': 'Возврат расходов на рекламу',
    'logistics_refund': 'Возврат расходов на логистику',
    'adjustment': 'Корректировка',
    'net': 'Итоговое начисление (без детализации)',
    'restock': 'Возврат изделия в пригодный запас (сумма 0)',
}


def import_operations(db, content: bytes, filename: str, rows: list[dict], mode='detail'):
    """Validate everything before writing. Stable IDs permit corrections, file hash prevents reimport."""
    digest = hashlib.sha256(content).hexdigest()
    if db.execute('SELECT 1 FROM imports WHERE digest=?', (digest,)).fetchone():
        return 0
    if not rows:
        raise ValueError('Нет строк для импорта')
    parsed = []
    seen = set()
    for index, row in enumerate(rows, 1):
        kind = row['kind']
        if kind not in KINDS:
            raise ValueError(f'Строка {index}: неизвестный тип')
        if (mode == 'net' and kind != 'net') or (mode == 'detail' and kind == 'net'):
            raise ValueError('Нельзя смешивать итоговые начисления и детализацию')
        day = str(row['day'])
        date.fromisoformat(day)
        amount, qty = money(row['amount']), money(row.get('qty', 0))
        if qty < 0:
            raise ValueError(f'Строка {index}: количество указывается положительным')
        if kind in ('sale', 'return', 'restock') and (qty <= 0 or not str(row.get('sku', '')).strip()):
            raise ValueError(f'Строка {index}: для продажи/возврата нужны артикул и количество')
        if kind in ('commission', 'logistics', 'advertising', 'service', 'return') and amount > 0:
            raise ValueError(f'Строка {index}: удержание или возврат выручки должен быть отрицательным; возмещение оформите корректировкой')
        if kind in ('sale', 'points') and amount < 0:
            raise ValueError(f'Строка {index}: продажа/баллы должны быть положительными')
        if kind == 'restock' and amount != 0:
            raise ValueError('Возврат в запас должен иметь нулевую сумму')
        op_id = str(row.get('id', '')).strip()
        if not op_id:
            raise ValueError(f'Строка {index}: нужен уникальный ID операции из отчёта')
        if op_id in seen:
            raise ValueError(f'Повтор ID {op_id}: выберите ID строки услуги или составьте ID из нескольких столбцов')
        seen.add(op_id)
        parsed.append((op_id, day, str(row.get('order_no', '')).strip(), str(row.get('sku', '')).strip(), str(row.get('name', '')).strip(), kind, str(amount), str(qty), digest))
    existing_mode = get_setting(db, 'ledger_mode')
    if existing_mode and existing_mode != mode:
        raise ValueError('База уже использует другой способ учёта. Итоги и детализацию нельзя складывать. Создайте отдельную базу для сверки.')
    with db:
        db.execute('INSERT INTO imports(digest,name) VALUES(?,?)', (digest, filename))
        db.executemany('''INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          day=excluded.day, order_no=excluded.order_no, sku=excluded.sku, name=excluded.name,
          kind=excluded.kind, amount=excluded.amount, qty=excluded.qty, source=excluded.source''', parsed)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('ledger_mode', mode))
    return len(parsed)


def calculate(db, start, end):
    rows = db.execute('SELECT * FROM operations WHERE day BETWEEN ? AND ? ORDER BY day,id', (str(start), str(end))).fetchall()
    rate = money(get_setting(db, 'tax_rate', '4')) / 100
    tax_base_mode = get_setting(db, 'tax_base', 'sales_points')
    totals = dict(net=ZERO, revenue=ZERO, costs=ZERO, tax=ZERO, profit=ZERO, expenses=ZERO, estimated=0, missing=0, count=len(rows))
    details = []
    for row in rows:
        amount, qty = money(row['amount']), money(row['qty'])
        cost, issue = ZERO, ''
        if row['kind'] in ('sale', 'restock'):
            if row['kind'] == 'restock':
                # Allocate returned units to original sale lots, retaining historical costs.
                sales = db.execute("SELECT * FROM operations WHERE order_no=? AND sku=? AND kind='sale' AND day<=? ORDER BY day,id", (row['order_no'], row['sku'], row['day'])).fetchall() if row['order_no'] else []
                prior = db.execute("SELECT qty FROM operations WHERE order_no=? AND sku=? AND kind='restock' AND (day<? OR (day=? AND id<?))", (row['order_no'],row['sku'],row['day'],row['day'],row['id'])).fetchall()
                used = sum((money(r['qty']) for r in prior), ZERO)
                remaining = qty
                return_cost = ZERO
                is_estimate = False
                for sale in sales:
                    available = money(sale['qty'])
                    skip = min(used, available)
                    used -= skip
                    take = min(remaining, available-skip)
                    if take <= 0:
                        continue
                    record = db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1', (row['sku'],sale['day'])).fetchone()
                    if not record:
                        issue = 'Не задана исходная себестоимость возврата'
                        break
                    return_cost += take * money(record['amount'])
                    is_estimate = is_estimate or bool(record['estimated'])
                    remaining -= take
                if remaining > 0 or issue:
                    totals['missing'] += 1
                    issue = issue or 'Нет исходной продажи или возвращено больше проданного'
                else:
                    cost = -return_cost
                    if is_estimate:
                        totals['estimated'] += 1
                        issue = 'Примерная себестоимость'
            else:
                record = db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1', (row['sku'], row['day'])).fetchone()
                if not record:
                    totals['missing'] += 1
                    issue = 'Не задана себестоимость на дату продажи'
                else:
                    cost = money(record['amount']) * qty
                    if record['estimated']:
                        totals['estimated'] += 1
                        issue = 'Примерная себестоимость'
        revenue = amount if row['kind'] in ('sale', 'return') else ZERO
        base = revenue
        if tax_base_mode in ('sales_points','sales_points_partner') and row['kind'] in ('points','points_reversal'):
            base = amount
        if tax_base_mode in ('sales_partner','sales_points_partner') and row['kind'] == 'partner':
            base = amount
        if tax_base_mode == 'net':
            base = amount
        tax = base * rate
        totals['net'] += amount
        totals['revenue'] += revenue
        totals['costs'] += cost
        totals['tax'] += tax
        if row['kind'] in ('commission','logistics','advertising','service','service_refund','advertising_refund','logistics_refund') or (row['kind']=='adjustment' and amount<0):
            totals['expenses'] -= amount
        details.append(dict(day=row['day'], order_no=row['order_no'], sku=row['sku'], name=row['name'], kind=KINDS[row['kind']], amount=amount, cost=cost, tax=tax, profit=amount-cost-tax, issue=issue))
    # Negative tax from returns cannot create a withdrawal reserve credit.
    raw_tax = totals['tax']
    totals['tax'] = max(ZERO, raw_tax)
    totals['tax_adjustment'] = totals['tax'] - raw_tax
    totals['profit'] = totals['net'] - totals['costs'] - totals['tax']
    totals['net_mode'] = get_setting(db, 'ledger_mode') == 'net'
    totals['incomplete'] = bool(totals['missing'] or totals['estimated'] or totals['net_mode'])
    totals['details'] = details
    return totals


def withdrawal(cash, tax, materials, obligations):
    return max(ZERO, money(cash) - money(tax) - money(materials) - money(obligations))
