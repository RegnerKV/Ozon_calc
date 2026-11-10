"""Standalone capability probes. No ledger imports, migrations or response dumps."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import UUID

import requests

ARTICLE = 'подвеска_наличник'
START = '2026-10-13'
END = '2026-11-09'
SELLER = 'https://api-seller.ozon.ru'
PERFORMANCE = 'https://api-performance.ozon.ru'
SELLER_PATHS = {
    '/v3/product/info/list', '/v3/posting/fbo/list', '/v4/posting/fbs/list',
    '/v1/analytics/data', '/v1/analytics/product-queries/details',
    '/v4/product/info/stocks', '/v1/analytics/stocks',
}
PERFORMANCE_PATHS = {
    ('POST', '/api/client/token'), ('GET', '/api/client/campaign'),
    ('POST', '/api/client/statistics/json'), ('GET', '/api/client/statistics/report'),
}


def default_data_directory():
    # Unlike core.data_dir(), this must not create a directory or an empty DB.
    override = os.environ.get('OZON_PROFIT_DATA_DIR')
    return Path(override) if override else Path(os.environ.get(
        'LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'OzonProfit'


def open_readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    db.execute('PRAGMA query_only=ON')
    return db


def read_connections(directory):
    """Read the v0.3.0-rc.3 DPAPI formats without core.connect()/schema init."""
    from . import credentials
    directory = Path(directory)
    pairs = {'seller': None, 'performance': None}
    states = {'seller': 'missing', 'performance': 'missing', 'database': 'missing'}
    skus = set()
    if (directory / 'seller.credentials').is_file():
        try:
            pairs['seller'] = credentials.load(directory / 'seller.credentials')
            states['seller'] = 'loaded'
        except Exception:
            states['seller'] = 'decrypt_failed'
    if not (directory / 'profit.db').is_file():
        return pairs, states, skus
    try:
        with closing(open_readonly(directory / 'profit.db')) as db:
            states['database'] = 'opened_read_only'
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'product_links' in tables:
                skus = {str(row[0]) for row in db.execute(
                    'SELECT ozon_sku FROM product_links WHERE sku=? LIMIT 100', (ARTICLE,))
                    if re.fullmatch(r'[1-9][0-9]{0,19}', str(row[0]))}
            if 'secure_items' in tables:
                row = db.execute('SELECT value FROM secure_items WHERE name=?',
                                 ('performance',)).fetchone()
                if row:
                    raw = bytes(row[0])
                    if raw.startswith(b'DPAPI:'):
                        try:
                            pair = json.loads(credentials._transform(raw[6:], decrypt=True))
                            if (not isinstance(pair, list) or len(pair) != 2 or
                                    any(not isinstance(v, str) or not v or len(v) > 4096 or
                                        any(c.isspace() for c in v) for v in pair)):
                                raise ValueError()
                            pairs['performance'] = tuple(pair)
                            states['performance'] = 'loaded'
                        except Exception:
                            states['performance'] = 'decrypt_failed'
                    else:
                        states['performance'] = ('web_password_required' if raw.startswith(b'WEB:')
                                                 else 'unknown_storage_format')
                if pairs['seller'] is None and db.execute(
                        'SELECT 1 FROM secure_items WHERE name=?', ('seller',)).fetchone():
                    states['seller'] = 'web_password_required'
    except (OSError, sqlite3.Error, TypeError, ValueError):
        states['database'] = 'read_failed'
    return pairs, states, skus


class Transport:
    """Fixed hosts/methods only; error text, headers and response values never exported."""
    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic):
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.last_analytics = None

    def close(self):
        self.session.close()

    def request(self, service, method, path, headers, **kwargs):
        if service == 'seller':
            allowed = method == 'POST' and path in SELLER_PATHS
            base = SELLER
        elif service == 'performance':
            allowed = (method, path) in PERFORMANCE_PATHS or (
                method == 'GET' and re.fullmatch(
                    r'/api/client/statistics/[0-9a-fA-F-]{36}', path))
            base = PERFORMANCE
        else:
            allowed = False
        if not allowed:
            raise ValueError('Method not allowed')
        if path == '/v1/analytics/data':
            if self.last_analytics is not None:
                self.sleep(max(0, 61 - (self.clock() - self.last_analytics)))
            self.last_analytics = self.clock()
        result = {'http_status': None, 'outcome': 'network_error',
                  'subscription_requirement': 'not_established'}
        try:
            with self.session.request(method, base + path, headers=headers,
                                      timeout=(10, 45), allow_redirects=False,
                                      stream=True, **kwargs) as response:
                result['http_status'] = int(response.status_code)
                if not 200 <= response.status_code < 300:
                    result['outcome'] = ('rate_limited' if response.status_code == 429
                                         else 'http_error')
                    return result, None
                # Bound even chunked responses; do not save downloaded data to disk.
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        result['outcome'] = 'response_too_large'
                        return result, None
                    chunks.append(chunk)
                data = json.loads(b''.join(chunks))
                result['outcome'] = 'response_received'
                return result, data
        except requests.exceptions.SSLError:
            result['outcome'] = 'tls_error'
        except requests.exceptions.Timeout:
            result['outcome'] = 'timeout'
        except requests.RequestException:
            pass
        except (ValueError, UnicodeError):
            result['outcome'] = 'invalid_json'
        return result, None


def numeric(value):
    # Numeric values themselves are deliberately not exported.
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def list_summary(data, key):
    batch = data.get(key) if isinstance(data, dict) else None
    if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
        return {'schema': 'unrecognized'}
    return {'schema': 'recognized', 'sample_rows': len(batch),
            'data_presence': 'nonempty' if batch else 'empty_not_proof_of_unavailability'}


def analytics_summary(data, sku, metric):
    result = data.get('result') if isinstance(data, dict) else None
    summary = list_summary(result, 'data')
    if summary['schema'] != 'recognized':
        return summary
    rows = result['data']
    days = set()
    for row in rows:
        dimensions, metrics = row.get('dimensions'), row.get('metrics')
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                not all(isinstance(v, dict) for v in dimensions) or
                str(dimensions[0].get('id')) != sku or
                not isinstance(metrics, list) or len(metrics) != 1 or not numeric(metrics[0])):
            return {'schema': 'unrecognized'}
        day = dimensions[1].get('id')
        try:
            if not START <= date.fromisoformat(day).isoformat() <= END or day in days:
                raise ValueError()
        except (ValueError, TypeError):
            return {'schema': 'unrecognized'}
        days.add(day)
    return {**summary, 'metric': metric, 'sku_day_rows_verified': len(days),
            'full_period_coverage': len(days) == 28,
            'scope': 'one_resolved_sku_first_page'}


def performance_summary(data, skus):
    """Accept documented report containers; unknown schema is never zero statistics."""
    # JSON reports can be a campaign-id map of {report: {rows: [...]}}.
    groups = data
    if isinstance(groups, dict) and 'rows' in groups:
        groups = [groups]
    elif isinstance(groups, dict) and groups and all(
            re.fullmatch(r'[0-9]+', str(k)) for k in groups):
        groups = list(groups.values())
    else:
        return {'schema': 'unrecognized'}
    if not isinstance(groups, list):
        return {'schema': 'unrecognized'}
    rows = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get('report'), dict):
            group = group['report']
        if list_summary(group, 'rows')['schema'] != 'recognized':
            return {'schema': 'unrecognized'}
        rows.extend(group['rows'])
    selected, days = [], set()
    if rows and not all(re.fullmatch(r'[1-9][0-9]{0,19}', str(row.get('sku')))
                        for row in rows):
        return {'schema': 'unrecognized', 'reason': 'SKU_granularity_not_verified'}
    for row in rows:
        if str(row.get('sku')) not in skus:
            continue
        raw_day = row.get('date')
        try:
            day = date.fromisoformat(raw_day).isoformat()
            if not START <= day <= END:
                raise ValueError()
        except (ValueError, TypeError):
            return {'schema': 'unrecognized', 'reason': 'target_day_not_verified'}
        days.add(day)
        selected.append(row)
    fields = {name: {'present_rows': sum(name in row for row in selected),
                     'numeric_rows': sum(numeric(row.get(name)) for row in selected)}
              for name in ('views', 'clicks', 'toCart', 'expense', 'orders', 'modelOrders')}
    return {'schema': 'recognized', 'target_rows': len(selected),
            'target_presence': ('observed' if selected else 'not_observed_not_zero'),
            'sku_day_granularity': 'verified_for_target' if selected else 'not_verified_for_target',
            'target_days_observed': len(days), 'fields': fields,
            'expense_scope': 'CPC_campaign_only',
            'orders_scope': 'Performance_attribution_not_all_source_sales',
            'totals_computed': False}


def run(directory=None, *, transport=None, offline=False, progress=lambda message: None,
        max_campaigns=20):
    pairs, states, skus = read_connections(directory or default_data_directory())
    report = {
        'schema_version': 1, 'tool': 'OzonApiDiagnostics',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'period': {'from': START, 'to': END, 'timezone': 'Europe/Moscow'},
        'control_article': ARTICLE, 'connections': states,
        'database_access': 'read_only_no_migrations_no_finance_queries',
        'subscription': 'unknown_not_inferred_from_HTTP_status',
        'owner_reference_not_api_result': {
            'advertising_clicks_approx': 1427, 'advertising_add_to_cart': 64,
            'CPC_expense_rub_approx': 2059, 'sold_units_all_sources': 11,
            'advertising_attributed_orders': None,
            'conversion_computed': False},
        'scope': 'capability_samples_not_full_import_or_financial_reconciliation',
        'sku_resolution': 'local_mapping_unverified' if skus else 'unresolved',
        'checks': [],
    }
    if offline:
        report['execution'] = 'offline_no_API_requests'
        return report
    transport = transport or Transport()
    checks = report['checks']

    def probe(name, service, method, path, headers, **kwargs):
        progress(name)
        outcome, data = transport.request(service, method, path, headers, **kwargs)
        entry = {'name': name, 'service': service, 'method': method,
                 # No UUIDs, request payloads or query parameters in the report.
                 'endpoint': ('/api/client/statistics/{UUID}' if re.fullmatch(
                     r'/api/client/statistics/[0-9a-fA-F-]{36}', path) else path), **outcome}
        checks.append(entry)
        return entry, data

    try:
        if pairs['seller']:
            headers = dict(zip(('Client-Id', 'Api-Key'), pairs['seller']))
            entry, data = probe('resolve_control_product', 'seller', 'POST',
                                '/v3/product/info/list', headers, json={'offer_id': [ARTICLE]})
            entry['sample'] = list_summary(data, 'items')
            if entry['sample']['schema'] == 'recognized':
                live_skus = set()
                for item in data['items']:
                    if item.get('offer_id') != ARTICLE:
                        continue
                    sources = item.get('sources', [])
                    if not isinstance(sources, list):
                        sources = []
                    candidates = [item.get('sku')] + [source.get('sku') for source in
                        sources if isinstance(source, dict)]
                    live_skus.update(str(v) for v in candidates if re.fullmatch(
                        r'[1-9][0-9]{0,19}', str(v)))
                if live_skus:
                    skus = live_skus
                    report['sku_resolution'] = 'live_product_info'
            for scheme, path in [('fbo', '/v3/posting/fbo/list'),
                                 ('fbs', '/v4/posting/fbs/list')]:
                entry, data = probe('orders_' + scheme, 'seller', 'POST', path, headers,
                    json={'cursor': '', 'sort_dir': 'ASC', 'limit': 1,
                          'filter': {'since': START + 'T00:00:00+03:00',
                                     'to': END + 'T23:59:59.999999+03:00'},
                          'with': {'analytics_data': False, 'financial_data': False,
                                   'legal_info': False}})
                entry['sample'] = list_summary(data, 'postings')
                entry['scope'] = 'one_posting_storewide_no_order_details_exported'
            entry, data = probe('product_stocks', 'seller', 'POST',
                '/v4/product/info/stocks', headers,
                json={'filter': {'offer_id': [ARTICLE], 'visibility': 'ALL'}, 'limit': 100})
            entry['sample'] = list_summary(data, 'items')
            if skus:
                entry, data = probe('FBO_stock_analytics', 'seller', 'POST',
                    '/v1/analytics/stocks', headers, json={'skus': sorted(skus)})
                entry['sample'] = list_summary(data, 'items')
                entry, data = probe('product_search_queries', 'seller', 'POST',
                    '/v1/analytics/product-queries/details', headers,
                    json={'date_from': START + 'T00:00:00+03:00',
                          'date_to': END + 'T23:59:59+03:00', 'skus': sorted(skus),
                          'page': 0, 'page_size': 100, 'limit_by_sku': 15})
                entry['sample'] = list_summary(data, 'queries')
                # Export field availability, never query text or its metric values.
                if entry['sample']['schema'] == 'recognized':
                    entry['sample']['fields_with_non_null_values'] = {
                        name: sum(row.get(name) is not None for row in data['queries'])
                        for name in ('position', 'unique_search_users', 'unique_view_users',
                                     'view_conversion', 'order_count')}
                for metric in ('ordered_units', 'hits_view', 'hits_tocart'):
                    entry, data = probe('seller_analytics_' + metric, 'seller', 'POST',
                        '/v1/analytics/data', headers,
                        json={'date_from': START, 'date_to': END, 'metrics': [metric],
                              'dimension': ['sku', 'day'], 'limit': 1000, 'offset': 0,
                              'filters': [{'key': 'sku', 'op': 'EQ', 'value': sorted(skus)[0]}],
                              'sort': []})
                    entry['sample'] = analytics_summary(data, sorted(skus)[0], metric)
            else:
                checks.append({'name': 'seller_SKU_probes', 'outcome': 'not_run_SKU_unresolved'})
        else:
            checks.append({'name': 'seller', 'outcome': 'not_run_credentials_unavailable'})
        if pairs['performance']:
            entry, data = probe('performance_token', 'performance', 'POST',
                '/api/client/token', {}, json={'client_id': pairs['performance'][0],
                    'client_secret': pairs['performance'][1], 'grant_type': 'client_credentials'})
            token = data.get('access_token') if isinstance(data, dict) else None
            if isinstance(token, str) and token and not any(c.isspace() for c in token):
                headers = {'Authorization': 'Bearer ' + token}
                _performance_checks(probe, transport, headers, skus, max_campaigns)
            else:
                checks.append({'name': 'performance_statistics', 'outcome': 'not_run_no_token'})
        else:
            checks.append({'name': 'performance', 'outcome': 'not_run_credentials_unavailable'})
        report['resolved_control_sku_count'] = len(skus)
    finally:
        transport.close()
        pairs.clear()
    return report


def _performance_checks(probe, transport, headers, skus, max_campaigns):
    campaigns = []
    complete = False
    for page in range(1, 21):
        entry, data = probe('performance_campaigns', 'performance', 'GET',
            '/api/client/campaign', headers, params={'page': page, 'pageSize': 100})
        entry['sample'] = list_summary(data, 'list')
        if entry['sample']['schema'] != 'recognized':
            return
        for row in data['list']:
            if (row.get('paymentType') == 'CPC' and
                    re.fullmatch(r'[1-9][0-9]{0,19}', str(row.get('id')))):
                campaigns.append(str(row['id']))
        if len(data['list']) < 100:
            complete = True
            break
    campaigns = sorted(set(campaigns))
    entry['CPC_campaigns_found'] = len(campaigns)
    entry['campaign_listing_complete'] = complete
    entry['campaign_reports_limited'] = len(campaigns) > max_campaigns
    if not campaigns:
        entry['statistics_probe'] = 'not_run_no_CPC_campaigns_found_not_proof_of_no_historical_spend'
    if not skus:
        entry['target_validation'] = 'SKU_unresolved_no_product_attribution'
    # One campaign at a time respects the published one-export/account limit.
    for campaign in campaigns[:max_campaigns]:
        entry, data = probe('CPC_SKU_day_generate', 'performance', 'POST',
            '/api/client/statistics/json', headers,
            json={'campaigns': [campaign], 'dateFrom': START, 'dateTo': END, 'groupBy': 'DATE'})
        try:
            identifier = str(UUID(data['UUID']))
        except (TypeError, KeyError, ValueError, AttributeError):
            entry['report_generation'] = 'UUID_not_received'
            continue
        for attempt in range(12):
            transport.sleep(5)
            entry, status = probe('CPC_report_status', 'performance', 'GET',
                '/api/client/statistics/' + identifier, headers)
            state = status.get('state') if isinstance(status, dict) else None
            entry['report_state'] = state if state in (
                'OK', 'ERROR', 'NOT_STARTED', 'IN_PROGRESS') else 'unknown'
            if state == 'OK':
                entry, data = probe('CPC_SKU_day_report', 'performance', 'GET',
                    '/api/client/statistics/report', headers, params={'UUID': identifier})
                entry['sample'] = performance_summary(data, skus)
                break
            if state not in ('NOT_STARTED', 'IN_PROGRESS'):
                return
        else:
            entry['outcome'] = 'pending_not_failed_no_more_exports_started'
            return
