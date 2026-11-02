"""Read selected-period staged data without requiring whole-history promotion."""
from datetime import date
from .core import calculate,get_setting,money
from .api_ledger import project,preview


def latest_period(db,today):
    days=[db.execute('SELECT max(day) FROM operations').fetchone()[0],
          db.execute('SELECT max(day) FROM api_finance').fetchone()[0]]
    last=max((d for d in days if d),default=str(today))
    end=date.fromisoformat(last)
    start=end.replace(day=1)
    covered=get_setting(db,'api_start')
    if covered and start<date.fromisoformat(covered)<=end:
        first_ledger=db.execute('SELECT min(day) FROM operations WHERE day BETWEEN ? AND ?',(str(start),str(end))).fetchone()[0]
        if not first_ledger or first_ledger>=covered:start=date.fromisoformat(covered)
    return start,end


def snapshot(db,start,end):
    result=calculate(db,start,end)
    records=db.execute('SELECT amount FROM api_finance WHERE day BETWEEN ? AND ?',(str(start),str(end))).fetchall()
    if not records or not get_setting(db,'api_last_success'):
        return result,[],None
    projection=project(db,start,end)
    if projection.issues:
        # Only the raw net is trustworthy until all components reconcile.
        return result,projection.issues,sum((money(r[0]) for r in records),money(0))
    return preview(db,projection,tax_base=get_setting(db,'tax_base','sales')),[],None
