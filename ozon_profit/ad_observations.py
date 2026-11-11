"""Read-only advertising statistics, separate from the financial ledger."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from uuid import UUID
from .seller_api import ApiError
from .performance import PerformanceHttpError

FIELDS = ('views', 'clicks', 'toCart', 'expense', 'orders', 'modelOrders')


def number(raw, field):
    if raw is None or isinstance(raw, bool) or str(raw).strip() == '':
        return None
    try:
        value = Decimal(str(raw).replace('\u00a0', '').replace(' ', '').replace(',', '.'))
        if not value.is_finite() or value < 0 or (field != 'expense' and value != value.to_integral_value()):
            raise ValueError()
        return str(value)
    except (ValueError, InvalidOperation):
        raise ApiError('Некорректный показатель рекламного отчёта.') from None


def payment(row):
    types = {row[k].strip().upper() for k in ('paymentType', 'payment_type') if isinstance(row.get(k), str)}
    objects = {row[k].strip().upper() for k in ('advObjectType', 'adv_object_type') if isinstance(row.get(k), str)}
    if types.intersection({'CPO', 'CPM'}) or objects.intersection({'SEARCH_PROMO', 'BANNER'}):
        return 'excluded'
    return 'CPC' if types == {'CPC'} else 'unknown'


def parse_report(data, campaign, skus, start, end):
    if isinstance(data, dict) and ('rows' in data or isinstance(data.get('report'), dict)):
        group = data
    elif isinstance(data, dict) and set(data) == {campaign}:
        group = data[campaign]
    else:
        raise ApiError('Не распознан отчёт кампании; данные не заменены нулями.')
    if isinstance(group, dict) and isinstance(group.get('report'), dict): group = group['report']
    rows = group.get('rows') if isinstance(group, dict) else None
    if not isinstance(rows, list): raise ApiError('В отчёте нет строк SKU/день.')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r'[1-9][0-9]{0,19}', str(row.get('sku'))):
            raise ApiError('Не подтверждена детализация рекламного отчёта по SKU.')
        sku = str(row['sku'])
        if sku not in skus: continue
        try:
            raw = row['date']
            try: day = date.fromisoformat(raw).isoformat()
            except ValueError: day = datetime.strptime(raw, '%d.%m.%Y').date().isoformat()
            if not start <= day <= end or (sku, day) in seen: raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ApiError('Повтор SKU/день или дата вне периода в рекламном отчёте.') from None
        seen.add((sku, day))
        result.append({'day': day, 'sku': sku, **{k: number(row.get(k), k) for k in FIELDS}})
    return result


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS ad_observation_runs(article TEXT,start TEXT,end TEXT,payload TEXT,PRIMARY KEY(article,start,end))')
    db.commit()


def save(db, run):
    init(db)
    with db:
        db.execute('INSERT OR REPLACE INTO ad_observation_runs VALUES(?,?,?,?)',
            (run['article'], run['start'], run['end'], json.dumps(run, ensure_ascii=False)))


def load(db, article, start, end):
    init(db)
    row = db.execute('SELECT payload FROM ad_observation_runs WHERE article=? AND start=? AND end=?',
                     (article, str(start), str(end))).fetchone()
    return json.loads(row[0]) if row else None


def diagnostic_snapshot(data):
    """Keep target observations only, never persist the full uploaded document."""
    if not isinstance(data, dict) or data.get('schema_version') != 3 or data.get('tool') != 'OzonApiDiagnostics':
        raise ApiError('Нужен отчёт OzonApiDiagnostics версии 3.')
    try:
        start, end = (date.fromisoformat(data['period'][k]).isoformat() for k in ('from', 'to'))
        article, coverage = data['control_article'], data['performance_coverage']
        if end < start or not isinstance(article, str) or not article or len(article) > 200: raise ValueError()
        eligible, received = coverage['eligible_campaigns'], coverage['reports_received']
        if any(type(v) is not int or v < 0 for v in (eligible, received)) or received > eligible: raise ValueError()
        reports = []
        for check in data['checks']:
            sample = check.get('sample', {})
            if not sample.get('target_rows'): continue
            stats = sample.get('target_daily_statistics', {})
            if stats.get('state') != 'parsed': raise ValueError()
            rows, seen = [], set()
            for row in stats['days']:
                day = date.fromisoformat(row['day']).isoformat()
                if not start <= day <= end or day in seen: raise ValueError()
                seen.add(day)
                rows.append({'day': day, **{k: number(row.get(k), k) for k in FIELDS}})
            reports.append({'campaign': 'Отчёт ' + str(len(reports)+1), 'payment': 'unknown', 'rows': rows})
        if len(reports) != coverage['target_reports_observed'] or len(reports) > received: raise ValueError()
        return dict(article=article, start=start, end=end, source='diagnostic_v3',
                    updated=data.get('created_at', ''), listing_complete=coverage['campaign_listing_complete'] is True,
                    eligible=eligible, received=received, reports=reports, jobs=[], complete=False)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ApiError('Не распознаны период, полнота или дневные показатели диагностики.') from None


def summary(run):
    rows = [r for report in run['reports'] for r in report['rows']]
    result = {}
    for field in FIELDS:
        known = [Decimal(r[field]) for r in rows if r.get(field) is not None]
        result[field] = dict(observed=str(sum(known)) if known else None,
                             known_rows=len(known), missing_rows=len(rows)-len(known))
    # Attribution may overlap: never sum orders across campaigns.
    result['orders']['observed'] = None
    result['modelOrders']['observed'] = None
    result['expense']['cpc_verified'] = bool(run['reports']) and all(r['payment'] == 'CPC' for r in run['reports'])
    return result


def advance(db, article, skus, start, end, client, batch=3):
    """Persist export jobs; one active export, no blocking polling loops."""
    start, end = str(start), str(end)
    if not 0 <= (date.fromisoformat(end)-date.fromisoformat(start)).days <= 30:
        raise ApiError('Для рекламного отчёта выберите период не длиннее 31 дня.')
    if not skus: raise ApiError('Не найден SKU артикула. Сначала обновите каталог магазина.')
    run = load(db, article, start, end)
    if run and run['source'] == 'live' and set(run['skus']) != set(skus):
        raise ApiError('Изменилось сопоставление SKU; проверьте прежнюю загрузку.')
    init(db)
    for row in db.execute('SELECT payload FROM ad_observation_runs'):
        other=json.loads(row[0])
        if (other['article'],other['start'],other['end']) == (article,start,end): continue
        if any(j['state'] in ('pending','submitting','uncertain') for j in other.get('jobs',[])):
            raise ApiError('Сначала завершите начатый рекламный отчёт для другого артикула или периода.')
    client.authorize()
    headers = {'Authorization': 'Bearer ' + client.token}
    if run is None or run['source'] != 'live':
        campaigns = {}
        for object_type in (None, 'SKU'):
            seen = set()
            for page in range(1, 21):
                params = {'page': page, 'pageSize': 100}
                if object_type: params['advObjectType'] = object_type
                data = client.request('GET', '/api/client/campaign', params=params, headers=headers)
                rows = data.get('list') if isinstance(data, dict) else None
                if not isinstance(rows, list): raise ApiError('Не распознан список кампаний.')
                for row in rows:
                    if not isinstance(row, dict): raise ApiError('Не распознана кампания.')
                    identifier = str(row.get('id'))
                    if not re.fullmatch(r'[1-9][0-9]{0,19}', identifier): raise ApiError('Не распознан ID кампании.')
                    scope, previous = payment(row), campaigns.get(identifier)
                    campaigns[identifier] = ('excluded' if 'excluded' in (previous, scope) else
                        'CPC' if previous in (None, 'CPC') and scope == 'CPC' else 'unknown')
                ids = tuple(str(r.get('id')) for r in rows)
                if len(rows) < 100: break
                if ids in seen: raise ApiError('Список кампаний повторяет страницу; полнота не установлена.')
                seen.add(ids)
            else: raise ApiError('Список кампаний не загружен полностью.')
        jobs = [dict(campaign=k, payment=v, state='new') for k,v in sorted(campaigns.items()) if v != 'excluded']
        run = dict(article=article, skus=sorted(skus), start=start, end=end, source='live', listing_complete=True,
                   eligible=len(jobs), received=0, reports=[], jobs=jobs, complete=False)
        save(db, run)
    for _ in range(batch):
        job = next((j for j in run['jobs'] if j['state'] not in ('done','rejected')), None)
        if job is None: break
        if job['state'] in ('submitting','uncertain'):
            raise ApiError('При отправке отчёта результат не подтверждён. Новые экспорты заблокированы, чтобы не дублировать запрос.')
        if job['state'] == 'new':
            job['state']='submitting'; save(db,run)
            try:
                data = client.request('POST', '/api/client/statistics/json', headers=headers,
                    json={'campaigns':[job['campaign']], 'dateFrom':start, 'dateTo':end, 'groupBy':'DATE'})
            except PerformanceHttpError as exc:
                # An explicit invalid request was rejected; it did not start an export.
                # Keep the gap visible and continue with other historical campaigns.
                if exc.status not in (400,404,422): raise
                job['state']='rejected'; job['http_status']=exc.status
                save(db,run)
                continue
            try: job['uuid'] = str(UUID(data['UUID']))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ApiError('Не получен идентификатор рекламного отчёта.') from None
            job['state'] = 'pending'; save(db, run)
        status = client.request('GET', '/api/client/statistics/'+job['uuid'], headers=headers)
        state = status.get('state') if isinstance(status, dict) else None
        if state in ('NOT_STARTED', 'IN_PROGRESS'): break
        if state != 'OK': raise ApiError('Отчёт не готов или завершился ошибкой; сохранённая загрузка не удалена.')
        data = client.request('GET', '/api/client/statistics/report', params={'UUID':job['uuid']}, headers=headers)
        rows = parse_report(data, job['campaign'], set(skus), start, end)
        run['reports'].append(dict(campaign=job['campaign'], payment=job['payment'], rows=rows))
        job['state'] = 'done'; run['received'] += 1
        run['complete'] = bool(run['eligible']) and run['received'] == run['eligible']
        run['updated'] = datetime.now(timezone.utc).isoformat(); save(db, run)
    return run


def effectiveness(run):
    """Evidence-based interpretation; no profitability or funnel-causality guesses."""
    values=summary(run)
    rows=[row for report in run['reports'] for row in report['rows']]
    paired=[r for r in rows if r.get('clicks') is not None and r.get('toCart') is not None]
    clicks=sum((Decimal(r['clicks']) for r in paired),Decimal(0))
    carts=sum((Decimal(r['toCart']) for r in paired),Decimal(0))
    ratio=carts/clicks*100 if paired and clicks>0 else None
    reports_with_orders=sum(any(r.get('orders') is not None and Decimal(r['orders'])>0
                               for r in report['rows']) for report in run['reports'])
    positive_cart=any(r.get('toCart') is not None and Decimal(r['toCart'])>0 for r in rows)
    if positive_cart:
        interest='В рекламных отчётах наряду с кликами есть добавления в корзину. Это не подтверждает покупку или окупаемость.'
    elif values['toCart']['known_rows']:
        interest='В известных строках добавлений в корзину не обнаружено. Пропуски и неполное покрытие не позволяют распространить этот вывод на весь период.'
    else:
        interest='Данных о добавлениях в корзину нет; оценить действия после кликов нельзя.'
    gaps=[]
    if not run.get('listing_complete') or run['received']<run['eligible']:
        gaps.append(f"Получены не все отчёты ({run['received']} из {run['eligible']}). Завершите загрузку кампаний.")
    if values['expense']['observed'] is None:
        gaps.append('В полученных строках нет расходов. Для оценки затрат нужен расход за тот же товар, период и тип оплаты.')
    elif not values['expense']['cpc_verified']:
        gaps.append('Тип оплаты не подтверждён: расход нельзя считать оплатой только за клики.')
    if not reports_with_orders:
        gaps.append('Подтверждённого итога рекламных заказов нет. Реализованные изделия из всех источников не заменяют рекламную атрибуцию.')
    else:
        gaps.append(f'Положительные значения заказов есть в {reports_with_orders} отчётах. Они могут пересекаться; общий итог без проверки атрибуции не определён.')
    return dict(interest=interest,paired_rows=len(paired),paired_clicks=clicks,paired_carts=carts,
                cart_events_per_100_clicks=ratio,gaps=gaps,
                verdict='Окупаемость рекламы не установлена',
                action='Не меняйте рекламный бюджет только по этим цифрам: сначала сопоставьте расходы с выручкой по рекламной атрибуции за одинаковый период. Текущие данные не доказывают ни убыточность рекламы, ни причину потери покупателей.')
