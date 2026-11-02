"""Adapters for native Ozon XLSX reports. Documents are data, never instructions.

Financial exports are complete period snapshots, not streams of unique operation IDs.
ID начисления is a posting/service reference and is deliberately NOT a primary key.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import hashlib
import json
import re

from openpyxl import load_workbook
from .excel import workbook, text_cell
from .core import ZERO, connect, money, import_operations, get_setting

PREFIX = 'ozon-xlsx:'


def load(content):
    workbook(content)  # size and archive limits, before openpyxl
    return load_workbook(BytesIO(content), data_only=True, read_only=True)


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '').replace('\u00a0',' ')).strip()


def period_from(text):
    matches = re.findall(r'\d{2}\.\d{2}\.\d{4}',text)
    if len(matches) < 2:
        raise ValueError('Не удалось определить период из заголовка отчёта')
    first, last = [datetime.strptime(v,'%d.%m.%Y').date() for v in matches[:2]]
    if first > last:
        raise ValueError('Некорректный период отчёта')
    return first,last


@dataclass
class AccrualReport:
    start: date
    end: date
    rows: list
    categories: dict
    products: dict
    raw_count: int

    @property
    def net(self):
        return sum((r['amount'] for r in self.rows),ZERO)


def classify(group, label, amount):
    if label == 'Выручка':
        return 'sale' if amount >= 0 else 'return'
    if label == 'Баллы за скидки':
        return 'points' if amount >= 0 else 'points_reversal'
    if label == 'Программы партнёров':
        return 'partner'
    if 'компенсац' in label.lower():
        return 'compensation'
    if 'возврат' in label.lower() and 'выручк' in label.lower():
        return 'return'
    if group == 'Вознаграждение Ozon':
        return 'commission' if amount <= 0 else 'service_refund'
    if group == 'Продвижение и реклама':
        return 'advertising' if amount <= 0 else 'advertising_refund'
    if group == 'Услуги доставки':
        return 'logistics' if amount <= 0 else 'logistics_refund'
    if group in ('Услуги партнёров','Другие услуги и штрафы'):
        return 'service' if amount <= 0 else 'service_refund'
    raise ValueError(f'Новый тип начисления «{label}» / «{group}». Нужна проверка правил импорта; файл не импортирован.')


def parse_accruals(content):
    book=load(content)
    try:
        if 'Начисления' not in book.sheetnames:
            raise ValueError('В финансовом отчёте не найден лист «Начисления»')
        values=list(book['Начисления'].iter_rows(values_only=True))
        header=None
        for i,r in enumerate(values[:30]):
            if 'ID начисления' in r and 'Дата начисления' in r and 'Сумма итого, руб.' in r:
                header=i; break
        if header is None:
            raise ValueError('Не распознан заголовок оригинального отчёта Ozon. Предыдущая сверка не является отчётом начислений.')
        start,end=period_from(' '.join(norm(v) for r in values[:header] for v in r))
        cols={norm(v):i for i,v in enumerate(values[header]) if v is not None}
        required=['ID начисления','Дата начисления','Группа услуг','Тип начисления','Артикул','SKU','Название товара','Количество','Сумма итого, руб.']
        if any(k not in cols for k in required):
            raise ValueError('Не хватает обязательных столбцов финансового отчёта')
        grouped={}; categories=defaultdict(lambda:ZERO); products={}; raw_count=0
        for source in values[header+1:]:
            if not any(v is not None for v in source): continue
            def cell(key): return source[cols[key]]
            if not isinstance(cell('Дата начисления'),(datetime,date)):
                raise ValueError('Встречена строка без даты. Итоги/неизвестные строки не импортируются молча.')
            day=cell('Дата начисления').date() if isinstance(cell('Дата начисления'),datetime) else cell('Дата начисления')
            if not start<=day<=end: raise ValueError('Операция находится за пределами периода отчёта')
            if cell('Сумма итого, руб.') is None: raise ValueError('Пустая сумма операции')
            amount=money(cell('Сумма итого, руб.'))
            group,label=norm(cell('Группа услуг')),norm(cell('Тип начисления'))
            kind=classify(group,label,amount)
            article=text_cell(cell('Артикул') or '')
            ozon_sku=text_cell(cell('SKU') or '')
            name=norm(cell('Название товара'))
            if article:
                products[article]={'sku':article,'ozon_sku':ozon_sku,'name':name}
            reference=text_cell(cell('ID начисления') or '')
            # Date+type+product+reference uniquely identify an aggregate in the snapshot.
            # Repeated fees with blank IDs on the same day are summed, never overwritten.
            identity=json.dumps([day.isoformat(),group,label,article,ozon_sku,reference,kind],ensure_ascii=False)
            identifier=PREFIX+hashlib.sha256(identity.encode()).hexdigest()
            # Sales references are posting numbers; advertising campaign IDs aren't orders.
            posting=reference if article and re.fullmatch(r'\d+-\d+-\d+',reference) else ''
            qty=abs(money(cell('Количество'))) if kind in ('sale','return') else ZERO
            if identifier not in grouped:
                grouped[identifier]=dict(id=identifier,day=day.isoformat(),order_no=posting,sku=article,name=name,kind=kind,amount=ZERO,qty=ZERO)
            target=grouped[identifier]
            target['amount']+=amount;target['qty']+=qty
            categories[label]+=amount;raw_count+=1
        if not raw_count: raise ValueError('Отчёт не содержит финансовых операций')
        return AccrualReport(start,end,list(grouped.values()),dict(categories),products,raw_count)
    finally:
        book.close()


def parse_costs(content):
    book=load(content)
    try:
        if 'Украшения' not in book.sheetnames:
            raise ValueError('Не найден лист «Украшения»')
        result=[]; current=None
        for r in book['Украшения'].iter_rows(values_only=True):
            title=norm(r[0]) if r else ''
            if title=='Название':
                current=None
            elif title.lower().startswith('с упаковкой'):
                if not current or len(r)<5 or r[4] is None:
                    raise ValueError('Не удалось прочитать полный итог с упаковкой. Пересохраните файл в Excel с пересчётом формул.')
                amount=money(r[4])
                if amount<=0: raise ValueError('Проверьте итог себестоимости с упаковкой')
                result.append({'name':current,'amount':amount})
                current=None
            elif title and title.lower()!='итого':
                current=title
        if not result: raise ValueError('Не найдены итоги «С упаковкой»')
        if len({r['name'] for r in result})!=len(result): raise ValueError('Повтор названий изделий в себестоимости')
        return result
    finally:
        book.close()


@dataclass
class SalesReport:
    start: date
    end: date
    products: dict
    rows: int


def parse_sales(content):
    book=load(content)
    try:
        if 'Отчет о реализации' not in book.sheetnames: raise ValueError('Не найден лист «Отчет о реализации»')
        rows=list(book['Отчет о реализации'].iter_rows(values_only=True))
        period_line=next((norm(v) for r in rows[:12] for v in r if 'Реализация товаров за период' in norm(v)), '')
        start,end=period_from(period_line)
        header=next((i for i,r in enumerate(rows) if len(r)>3 and norm(r[2])=='Артикул' and norm(r[3])=='SKU'),None)
        if header is None: raise ValueError('Не распознан заголовок отчёта реализации')
        # Validate the second header tier before using the original report column layout.
        second=rows[header+1]
        expected={5:'Реализовано на сумму, руб.',6:'Выплаты по механикам лояльности партнёров, руб.',7:'Кол-во',9:'Возвращено на сумму, руб.',11:'Кол-во'}
        if any(norm(second[i])!=label for i,label in expected.items()): raise ValueError('Изменился формат отчёта реализации')
        products={}; count=0
        for r in rows[header+3:]:
            if not r or not isinstance(r[0],(int,float)):
                continue
            r=tuple(r)+(None,)*max(0,13-len(r))
            article=text_cell(r[2] or '')
            if not article: raise ValueError('В строке реализации отсутствует артикул')
            record=products.setdefault(article,dict(sku=article,ozon_sku=text_cell(r[3] or ''),name=norm(r[1]),sold=ZERO,returned=ZERO,revenue=ZERO,partner=ZERO))
            record['sold']+=money(r[7]);record['returned']+=money(r[11])
            record['revenue']+=money(r[5])-money(r[9])
            record['partner']+=money(r[6])-money(r[10]);count+=1
        if not count: raise ValueError('Нет строк реализации')
        return SalesReport(start,end,products,count)
    finally:
        book.close()


def reconcile(accruals,sales):
    if (accruals.start,accruals.end)!=(sales.start,sales.end): raise ValueError('Периоды двух отчётов различаются')
    actual=defaultdict(lambda:dict(revenue=ZERO,partner=ZERO,sold=ZERO,returned=ZERO))
    for row in accruals.rows:
        record=actual[row['sku']]
        if row['kind'] in ('sale','return'):
            record['revenue']+=row['amount']
            record['sold' if row['kind']=='sale' else 'returned']+=row['qty']
        if row['kind']=='partner':record['partner']+=row['amount']
    differences=[]
    for sku in set(actual)|set(sales.products):
        expected=sales.products.get(sku,{})
        for key in ('revenue','partner','sold','returned'):
            a=actual[sku][key];b=expected.get(key,ZERO)
            if a!=b:differences.append(dict(sku=sku,field=key,accruals=str(a),sales=str(b)))
    return differences


def suggested_costs(products,costs):
    """Only reviewed aliases; ambiguous variant is explicitly an estimate."""
    aliases={
        'подвеска_наличник':('Наличник синий',False,'Синий вариант подтверждён владельцем магазина'),
        'чокер_ночное_небо':('Чокер Ночное небо',False,'Сопоставлено по названию'),
        'подвеска_малина':('Подвеска Рыба',True,'Оценка по «Рыбе», не фактическая стоимость «Малины»'),
        'подвеска_малина_шнурок':('Подвеска Рыба',True,'Оценка по «Рыбе», не фактическая стоимость «Малины»'),
    }
    by_name={r['name']:r['amount'] for r in costs}
    result=[]
    for sku,p in products.items():
        def key(value):return re.sub(r'[^a-zа-я0-9]','',norm(value).casefold().replace('ё','е'))
        direct=next((name for name in by_name if key(name) in (key(p['name']),key(sku))), '')
        target,estimated,note=aliases.get(sku,(direct,not bool(direct),'Сопоставлено по названию' if direct else 'Нужно выбрать себестоимость'))
        result.append(dict(sku=sku,name=p['name'],cost_name=target if target in by_name else '',estimated=estimated,note=note))
    return result


def apply_report(db,content,filename,report,cost_records,since,tax_base):
    """Atomic replacement of this adapter's complete period snapshot + chosen costs.

    No raw workbook is retained. Old snapshots remain in the import audit, not the ledger.
    """
    date.fromisoformat(str(since))
    if tax_base not in ('sales','sales_points','sales_partner','sales_points_partner'):
        raise ValueError('Неизвестная налоговая база')
    checked=[]
    for r in cost_records:
        amount=money(r['amount'])
        if not r['sku'] or not r['name'] or amount<0:raise ValueError('Проверьте сопоставление себестоимости')
        checked.append((r['sku'],r['name'],str(amount),int(r['estimated']),r.get('note','')))
    digest=hashlib.sha256(content).hexdigest()
    if db.execute('SELECT 1 FROM imports WHERE digest=?',(digest,)).fetchone():
        return 0
    if get_setting(db,'ledger_mode','detail')!='detail':raise ValueError('Нельзя смешивать итоговые начисления и детализацию')
    if db.execute('SELECT 1 FROM operations WHERE day BETWEEN ? AND ? AND id NOT LIKE ? LIMIT 1',(str(report.start),str(report.end),PREFIX+'%')).fetchone():
        raise ValueError('В этом периоде уже есть операции ручного импорта. Автоимпорт остановлен для защиты от двойного учёта.')
    staging=connect(':memory:')
    try:
        import_operations(staging,content,filename,report.rows)
        records=[tuple(r) for r in staging.execute('SELECT * FROM operations')]
    finally:
        staging.close()
    with db:
        db.execute('INSERT INTO imports(digest,name) VALUES(?,?)',(digest,filename))
        db.execute('DELETE FROM operations WHERE day BETWEEN ? AND ? AND id LIKE ?',(str(report.start),str(report.end),PREFIX+'%'))
        db.executemany('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)',records)
        for article,product in report.products.items():
            if product.get('ozon_sku'):
                db.execute('INSERT OR REPLACE INTO product_links VALUES(?,?)',(product['ozon_sku'],article))
        for sku,name,amount,estimated,note in checked:
            db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET name=excluded.name',(sku,name))
            db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(sku,str(since),amount,estimated,note))
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('ledger_mode','detail'),('tax_base',tax_base),
            ('report_start',str(report.start)),('report_end',str(report.end)),
        ])
    return len(records)


def parse_previous(content):
    """Read only known cached summary cells; never evaluate Excel expressions."""
    book=load(content)
    try:
        if 'Обзор' not in book.sheetnames:raise ValueError('В предыдущей сверке не найден лист «Обзор»')
        names={
            'Реализация (налоговая база НПД, предварительно)':'revenue',
            'Итог начислений Ozon':'net',
            'Себестоимость продаж (включая оценки)':'costs',
            'Резерв НПД 4% с реализации':'tax',
            'Предварительная прибыль после учтённых расходов':'profit',
        }
        result={}
        for r in book['Обзор'].iter_rows(values_only=True):
            if r and norm(r[0]) in names:
                if len(r)<2 or r[1] is None:raise ValueError('В предыдущей сверке не сохранены результаты формул. Пересохраните файл в Excel.')
                result[names[norm(r[0])]]=money(r[1])
        if not result:raise ValueError('Не распознаны показатели предыдущей сверки')
        return result
    finally:
        book.close()


def preview_calculation(content,report,cost_records,since,tax_base,tax_rate):
    from .core import calculate, set_setting
    db=connect(':memory:')
    try:
        set_setting(db,'tax_rate',tax_rate)
        apply_report(db,content,'preview',report,cost_records,since,tax_base)
        return calculate(db,report.start,report.end)
    finally:
        db.close()
