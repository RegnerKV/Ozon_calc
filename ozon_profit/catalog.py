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
        for since in dates:
            costs=[dict(name=r[0],amount=money(r[1])) for r in db.execute('SELECT name,amount FROM cost_library WHERE since=?',(since,))]
            by_name={r['name']:r['amount'] for r in costs}
            for match in suggested_costs(products,costs):
                name=match['cost_name']
                if name not in by_name:continue
                existing=db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1',(match['sku'],since)).fetchone()
                if existing and not existing['note'].startswith('Из сохранённой таблицы:'):continue
                db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO NOTHING',(match['sku'],match['name']))
                db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(match['sku'],since,str(by_name[name]),int(match['estimated']),'Из сохранённой таблицы: '+name+'; '+match['note']))
