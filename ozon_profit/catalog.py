"""Persistent one-time cost library, matched to existing and future catalog entries."""
from datetime import date
from .core import money
from .ozon_excel import parse_costs,suggested_costs


def import_library(db,content,since):
    date.fromisoformat(str(since))
    costs=parse_costs(content)
    with db:
        db.executemany('INSERT OR REPLACE INTO cost_library VALUES(?,?,?)',[(r['name'],str(since),str(r['amount'])) for r in costs])
    apply_library(db)
    return len(costs)


def apply_library(db):
    products={r['sku']:{'sku':r['sku'],'name':r['name']} for r in db.execute('SELECT sku,name FROM products')}
    has_postings=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_postings'").fetchone()
    for row in (db.execute('SELECT payload FROM api_postings') if has_postings else []):
        import json
        for p in json.loads(row[0]).get('products',[]):
            if p.get('offer_id'):
                products[str(p['offer_id'])]={'sku':str(p['offer_id']),'name':str(p.get('name') or p['offer_id'])}
    dates=[r[0] for r in db.execute('SELECT DISTINCT since FROM cost_library ORDER BY since')]
    with db:
        for product in products.values():
            db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO NOTHING',(product['sku'],product['name']))
        for since in dates:
            costs=[dict(name=r[0],amount=money(r[1])) for r in db.execute('SELECT name,amount FROM cost_library WHERE since=?',(since,))]
            by_name={r['name']:r['amount'] for r in costs}
            for match in suggested_costs(products,costs):
                name=match['cost_name']
                if name not in by_name:continue
                existing=db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1',(match['sku'],since)).fetchone()
                if existing and not existing['note'].startswith(('Из сохранённой таблицы:',FISH_POLICY)):continue
                db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO NOTHING',(match['sku'],match['name']))
                db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(match['sku'],since,str(by_name[name]),int(match['estimated']),'Из сохранённой таблицы: '+name+'; '+match['note']))

    apply_fish_policy(db)


FISH_POLICY='Принято владельцем: себестоимость как у подвески «Рыба»'


def apply_fish_policy(db):
    """Owner-approved costing convention, kept separate from measured recipe costs."""
    from .core import get_setting
    if get_setting(db,'fish_cost_policy','0')!='1':return
    import re
    def key(value):return re.sub(r'[^а-яa-z0-9]','',value.casefold().replace('ё','е'))
    fish_names={'рыба','подвескарыба'}
    history=[(r['since'],r['amount']) for r in db.execute('SELECT * FROM cost_library ORDER BY since,name') if key(r['name']) in fish_names]
    if not history:
        history=[(r['since'],r['amount']) for r in db.execute('SELECT c.*,p.name FROM costs c JOIN products p ON p.sku=c.sku ORDER BY c.since') if key(r['name']) in fish_names and not r['note'].startswith(FISH_POLICY)]
    if not history:return
    rates=dict(history)
    earliest=min(rates)
    rates.setdefault('0001-01-01',rates[earliest])
    with db:
        for product in db.execute('SELECT sku,name FROM products').fetchall():
            sku=product['sku']
            rows=db.execute('SELECT * FROM costs WHERE sku=? ORDER BY since',(sku,)).fetchall()
            own=[]
            for row in rows:
                inherited=row['note'].startswith(FISH_POLICY) or (row['estimated'] and (row['note'].startswith('Из сохранённой таблицы: Подвеска Рыба;') or 'Оценка по «Рыбе»' in row['note']))
                if not inherited:own.append(row)
                elif not row['note'].startswith(FISH_POLICY):db.execute('DELETE FROM costs WHERE sku=? AND since=?',(sku,row['since']))
            if own:
                # A subsequently entered product cost supersedes inherited costs.
                db.execute('DELETE FROM costs WHERE sku=? AND since>=? AND note LIKE ?',(sku,own[0]['since'],FISH_POLICY+'%'))
                continue
            for since,amount in sorted(rates.items()):
                db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(sku,since,str(money(amount)),0,FISH_POLICY+'; с упаковкой'))
