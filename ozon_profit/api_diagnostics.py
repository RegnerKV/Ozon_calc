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
from email.utils import parsedate_to_datetime
from decimal import Decimal, InvalidOperation
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
    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic, progress=lambda message: None):
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.last_analytics = None
        self.last_performance = None
        self.progress = progress

    def close(self):
        self.session.close()

    def request(self, service, method, path, headers, **kwargs):
        history, waits = [], []
        for attempt in range(3):
            result, data = self._request_once(service, method, path, headers, **kwargs)
            history.append(result['http_status'])
            if result['http_status'] != 429 or attempt == 2:
                break
            delay = max(61, result.get('retry_after_seconds', 61))
            if delay > 300:
                result['retry_deferred'] = True
                break
            waits.append(delay)
            self.progress('HTTP_429_wait_' + str(delay) + 's_retry_' + str(attempt + 1))
            self.sleep(delay)
        result['attempts'] = len(history)
        result['http_status_history'] = history
        if waits:
            result['retry_wait_seconds'] = waits
        return result, data

    def _request_once(self, service, method, path, headers, **kwargs):
        if service == 'seller':
            allowed = method == 'POST' and path in SELLER_PATHS
            base = SELLER
        elif service == 'performance':
            allowed = (method, path) in PERFORMANCE_PATHS or (
                method == 'GET' and re.fullmatch(
                    r'/api/client/statistics/[0-9a-fA-F-]{36}', path)) or (
                method == 'GET' and re.fullmatch(
                    r'/api/client/campaign/[1-9][0-9]{0,19}/(?:v2/products|objects)', path))
            base = PERFORMANCE
        else:
            allowed = False
        if not allowed:
            raise ValueError('Method not allowed')
        if service == 'performance':
            if self.last_performance is not None:
                self.sleep(max(0, 1 - (self.clock() - self.last_performance)))
            self.last_performance = self.clock()
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
                    if response.status_code == 429:
                        result['retry_after_seconds'] = retry_delay(
                            getattr(response, 'headers', {}).get('Retry-After'))
                    elif response.status_code in (400, 403, 404):
                        # Fixed flags/codes only. Error messages may echo private data.
                        body = bytearray()
                        for chunk in response.iter_content(4096):
                            body.extend(chunk)
                            if len(body) > 65536:
                                break
                        if len(body) <= 65536:
                            try:
                                result['provider_error'] = safe_error_summary(json.loads(body))
                            except (ValueError, UnicodeError):
                                pass
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


def retry_delay(value):
    try:
        if value is not None and str(value).strip().isdigit():
            return min(int(value), 86400)
        if value:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(0, min(86400, math.ceil((stamp - datetime.now(timezone.utc)).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        pass
    return 61


def safe_error_summary(data):
    if not isinstance(data, dict):
        return {'structured_error': False}
    source = data['error'] if isinstance(data.get('error'), dict) else data
    code = source.get('code', source.get('error_code'))
    known = {'INVALID_ARGUMENT', 'PERMISSION_DENIED', 'UNAUTHENTICATED',
             'NOT_FOUND', 'RESOURCE_EXHAUSTED', 'FAILED_PRECONDITION', 'UNIMPLEMENTED'}
    text = ' '.join(source[key][:4096].casefold() for key in ('message', 'error', 'description')
                    if isinstance(source.get(key), str))
    return {'structured_error': True,
            'code': code if (isinstance(code, str) and code in known) or (
                type(code) is int and 0 <= code <= 16) else 'not_exported',
            'text_mentions_subscription': any(word in text for word in ('premium', 'подписк')),
            'text_mentions_metric': any(word in text for word in ('metric', 'метрик')),
            'text_mentions_campaign': any(word in text for word in ('campaign', 'кампан')),
            'text_mentions_date_or_period': any(word in text for word in ('date', 'period', 'дат', 'период')),
            'text_mentions_no_data': any(word in text for word in ('no data', 'нет данных', 'no statistics')),
            'text_mentions_unsupported': any(word in text for word in ('unsupported', 'not supported', 'не поддерж')),
            'subscription_requirement': 'not_established'}


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


def performance_summary(data, skus, payment_scope='CPC_campaign_only', include_metrics=False,
                        expected_campaign=None):
    """Accept documented report containers; unknown schema is never zero statistics."""
    # JSON reports can be a campaign-id map of {report: {rows: [...]}}.
    groups = data
    if isinstance(groups, dict) and ('rows' in groups or isinstance(groups.get('report'), dict)):
        groups = [groups]
    elif isinstance(groups, dict) and groups and all(
            re.fullmatch(r'[0-9]+', str(k)) for k in groups):
        if expected_campaign is not None and set(groups) != {expected_campaign}:
            return {'schema': 'unrecognized', 'reason': 'campaign_not_matching_request'}
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
            try:
                day = date.fromisoformat(raw_day).isoformat()
            except (TypeError, ValueError):
                day = datetime.strptime(raw_day, '%d.%m.%Y').date().isoformat()
            if not START <= day <= END:
                raise ValueError()
        except (ValueError, TypeError):
            return {'schema': 'unrecognized', 'reason': 'target_day_not_verified'}
        days.add(day)
        selected.append(row)
    fields = {name: {'present_rows': sum(name in row for row in selected),
                     'numeric_rows': sum(numeric(row.get(name)) for row in selected)}
              for name in ('views', 'clicks', 'toCart', 'expense', 'orders', 'modelOrders')}
    result = {'schema': 'recognized', 'target_rows': len(selected),
            'target_presence': ('observed' if selected else 'not_observed_not_zero'),
            'sku_day_granularity': 'verified_for_target' if selected else 'not_verified_for_target',
            'target_days_observed': len(days), 'fields': fields,
            'expense_scope': payment_scope,
            'orders_scope': 'Performance_attribution_not_all_source_sales',
            'totals_computed': False}
    if include_metrics:
        result['target_daily_statistics'] = target_statistics(selected)
        result['statistics_scope'] = 'one_campaign_target_SKUs_only_no_cross_campaign_order_sum'
    return result


def target_statistics(rows):
    """Only the selected product's advertising statistics; no order/ledger records."""
    days, seen = {}, set()
    for row in rows:
        raw = row['date']
        try:
            day = date.fromisoformat(raw).isoformat()
        except ValueError:
            day = datetime.strptime(raw, '%d.%m.%Y').date().isoformat()
        identity = (str(row['sku']), day)
        if identity in seen:
            return {'state': 'not_aggregated_duplicate_SKU_day_rows'}
        seen.add(identity)
        daily = days.setdefault(day, {key: Decimal(0) for key in
            ('clicks', 'toCart', 'expense', 'orders', 'modelOrders')})
        for key in daily:
            if daily[key] is None:
                continue
            raw_value = row.get(key)
            try:
                if raw_value is None or isinstance(raw_value, bool):
                    raise ValueError()
                value = Decimal(str(raw_value).replace(' ', '').replace('\u00a0', '').replace(',', '.'))
                if not value.is_finite() or value < 0 or (key != 'expense' and value != value.to_integral_value()):
                    raise ValueError()
                daily[key] += value
            except (ValueError, InvalidOperation):
                daily[key] = None
    return {'state': 'parsed', 'days': [
        {'day': day, **{key: str(value) if value is not None else None
                       for key, value in fields.items()}}
        for day, fields in sorted(days.items())],
        'expense_label': 'expense_as_reported_payment_scope_is_separate',
        'orders_label': 'Performance_attribution_not_all_source_realized_units',
        'cross_campaign_orders_summed': False, 'conversion_computed': False}


def run(directory=None, *, transport=None, offline=False, progress=lambda message: None,
        max_campaigns=2000):
    pairs, states, skus = read_connections(directory or default_data_directory())
    report = {
        'schema_version': 3, 'tool': 'OzonApiDiagnostics',
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
    transport = transport or Transport(progress=progress)
    checks = report['checks']

    def probe(name, service, method, path, headers, **kwargs):
        progress(name)
        outcome, data = transport.request(service, method, path, headers, **kwargs)
        if (service == 'performance' and outcome['http_status'] == 401 and
                'Authorization' in headers and pairs['performance']):
            refreshed, token_data = transport.request('performance', 'POST', '/api/client/token', {},
                json={'client_id': pairs['performance'][0], 'client_secret': pairs['performance'][1],
                      'grant_type': 'client_credentials'})
            checks.append({'name': 'performance_token_refresh', 'service': 'performance',
                           'method': 'POST', 'endpoint': '/api/client/token', **refreshed})
            new_token = token_data.get('access_token') if isinstance(token_data, dict) else None
            if isinstance(new_token, str) and new_token and not any(c.isspace() for c in new_token):
                headers['Authorization'] = 'Bearer ' + new_token
                outcome, data = transport.request(service, method, path, headers, **kwargs)
                outcome['after_token_refresh'] = True
        entry = {'name': name, 'service': service, 'method': method,
                 # No UUIDs, request payloads or query parameters in the report.
                 'endpoint': re.sub(r'/campaign/[0-9]+/', '/campaign/{campaignId}/',
                     '/api/client/statistics/{UUID}' if re.fullmatch(
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
                report['performance_coverage'] = _performance_checks(
                    probe, transport, headers, skus, max_campaigns)
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
    campaigns, unclassified, excluded = set(), set(), set()
    coverage = {'campaign_listing_complete': False, 'eligible_campaigns': 0,
                'reports_attempted': 0, 'reports_received': 0, 'target_reports_observed': 0,
                'all_eligible_reports_received': False,
                'current_membership_is_not_historical_proof': True,
                'cross_campaign_orders_summed': False, 'conversion_computed': False}
    field_counts = {key: {} for key in ('paymentType', 'payment_type', 'advObjectType', 'adv_object_type')}
    complete = True
    for object_filter in (None, "SKU"):
        if object_filter and campaigns:
            break
        query_complete = False
        for page in range(1, 21):
            params = {'page': page, 'pageSize': 100}
            if object_filter:
                params['advObjectType'] = object_filter
            entry, data = probe('performance_campaigns', 'performance', 'GET',
                '/api/client/campaign', headers, params=params)
            entry['requested_object_type'] = object_filter or 'default'
            entry['sample'] = list_summary(data, 'list')
            if entry['sample']['schema'] != 'recognized':
                break
            for row in data['list']:
                for key, counts in field_counts.items():
                    value = row.get(key)
                    if key not in row:
                        label = 'missing'
                    elif value is None:
                        label = 'null'
                    elif isinstance(value, str):
                        label = value.strip().upper()
                        if label not in {'CPC', 'CPO', 'CPM', 'SKU', 'BANNER', 'SEARCH_PROMO',
                                         'CAMPAIGN_TYPE_INVALID', 'CAMPAIGN_TYPE_UNKNOWN'}:
                            label = 'other_string_not_exported'
                    else:
                        label = 'non_string_not_interpreted'
                    counts[label] = counts.get(label, 0) + 1
                identifier = str(row.get('id'))
                if not re.fullmatch(r'[1-9][0-9]{0,19}', identifier):
                    continue
                classification = campaign_payment(row)
                if classification == 'CPC':
                    campaigns.add(identifier)
                elif classification == 'unverified':
                    unclassified.add(identifier)
                else:
                    excluded.add(identifier)
            if len(data['list']) < 100:
                query_complete = True
                break
        complete = complete and query_complete
    campaigns = sorted(campaigns - excluded)
    unclassified -= excluded
    entry['campaign_field_categories'] = field_counts
    entry['field_count_scope'] = 'response_rows_can_repeat_between_default_and_SKU_queries'
    entry['CPC_campaigns_found'] = len(campaigns)
    entry['campaign_listing_complete'] = complete
    entry['campaign_reports_limited'] = len(campaigns) > max_campaigns
    entry['unclassified_campaigns_found'] = len(unclassified)
    # Probe unknown formats without labelling their expenses as CPC.
    candidates = [(identifier, 'CPC_campaign_only') for identifier in campaigns]
    candidates += [(identifier, 'payment_type_unverified_no_CPC_totals')
                   for identifier in sorted(unclassified - set(campaigns))]
    entry['selected_report_samples'] = min(len(candidates), max_campaigns)
    entry['report_samples_limited'] = len(candidates) > max_campaigns
    coverage['eligible_campaigns'] = len(candidates)
    coverage['excluded_non_click_or_conflicting_campaigns'] = len(excluded)
    coverage['campaign_listing_complete'] = complete
    coverage['campaign_limit_applied'] = len(candidates) > max_campaigns
    # Inspect current membership for prioritization, never to exclude historical ads.
    selected = candidates[:max_campaigns]
    ordered = []
    for index, (campaign, scope) in enumerate(selected, 1):
        membership = current_campaign_membership(probe, campaign, headers, skus, index, len(selected))
        ordered.append((campaign, scope, membership))
    ordered.sort(key=lambda item: item[2] != 'target_present')
    coverage['current_target_campaigns'] = sum(item[2] == 'target_present' for item in ordered)
    if not candidates:
        entry['statistics_probe'] = 'not_run_no_CPC_campaigns_found_not_proof_of_no_historical_spend'
    if not skus:
        entry['target_validation'] = 'SKU_unresolved_no_product_attribution'
    # One campaign at a time respects the published one-export/account limit.
    for index, (campaign, scope, membership) in enumerate(ordered, 1):
        prefix = 'CPC' if scope == 'CPC_campaign_only' else 'unclassified_payment'
        entry, data = probe(prefix + '_SKU_day_generate_' + str(index) + '_of_' + str(len(ordered)), 'performance', 'POST',
            '/api/client/statistics/json', headers,
            json={'campaigns': [campaign], 'dateFrom': START, 'dateTo': END, 'groupBy': 'DATE'})
        entry['payment_scope'] = scope
        entry['current_membership'] = membership
        coverage['reports_attempted'] += 1
        try:
            identifier = str(UUID(data['UUID']))
        except (TypeError, KeyError, ValueError, AttributeError):
            entry['report_generation'] = 'UUID_not_received'
            continue
        for attempt in range(120):
            transport.sleep(5)
            entry, status = probe(prefix + '_report_status', 'performance', 'GET',
                '/api/client/statistics/' + identifier, headers)
            state = status.get('state') if isinstance(status, dict) else None
            entry['report_state'] = state if state in (
                'OK', 'ERROR', 'NOT_STARTED', 'IN_PROGRESS') else 'unknown'
            if state == 'OK':
                entry, data = probe(prefix + '_SKU_day_report', 'performance', 'GET',
                    '/api/client/statistics/report', headers, params={'UUID': identifier})
                entry['sample'] = performance_summary(data, skus, scope, include_metrics=True,
                                                      expected_campaign=campaign)
                if entry['sample']['schema'] == 'recognized':
                    coverage['reports_received'] += 1
                    coverage['target_reports_observed'] += entry['sample']['target_rows'] > 0
                break
            if state == 'ERROR':
                break
            if state not in ('NOT_STARTED', 'IN_PROGRESS'):
                coverage['stop_reason'] = 'report_state_unknown_no_overlapping_exports'
                return coverage
        else:
            entry['outcome'] = 'pending_not_failed_no_more_exports_started'
            coverage['stop_reason'] = 'report_pending_after_bounded_wait_no_overlapping_exports'
            return coverage
    coverage['all_eligible_reports_received'] = bool(candidates) and (
        complete and len(candidates) <= max_campaigns and
        coverage['reports_received'] == len(candidates))
    return coverage


def current_campaign_membership(probe, campaign, headers, skus, index, total):
    found, complete = False, False
    valid = True
    for page in range(1, 21):
        entry, data = probe('performance_current_products_' + str(index) + '_of_' + str(total),
            'performance', 'GET', '/api/client/campaign/' + campaign + '/v2/products', headers,
            params={'page': page, 'pageSize': 100})
        entry['sample'] = list_summary(data, 'products')
        if entry['sample']['schema'] != 'recognized':
            break
        batch = data['products']
        valid = valid and all(re.fullmatch(r'[1-9][0-9]{0,19}', str(row.get('sku'))) for row in batch)
        found = found or any(str(row.get('sku')) in skus for row in batch)
        entry['target_present'] = found
        if len(batch) < 100:
            complete = valid
            break
    if not complete and not found:
        entry, data = probe('performance_current_objects_' + str(index) + '_of_' + str(total),
            'performance', 'GET', '/api/client/campaign/' + campaign + '/objects', headers)
        entry['sample'] = list_summary(data, 'list')
        if entry['sample']['schema'] == 'recognized':
            batch = data['list']
            complete = all(re.fullmatch(r'[1-9][0-9]{0,19}', str(row.get('id'))) for row in batch)
            found = any(str(row.get('id')) in skus for row in batch)
            entry['target_present'] = found
    state = 'target_present' if found else 'not_currently_listed' if complete else 'unknown'
    entry['current_membership'] = state
    entry['does_not_exclude_historical_membership'] = True
    return state


def campaign_payment(row):
    payments = {row[key].strip().upper() for key in ('paymentType', 'payment_type')
                if isinstance(row.get(key), str)}
    objects = {row[key].strip().upper() for key in ('advObjectType', 'adv_object_type')
               if isinstance(row.get(key), str)}
    # Conflicts and explicit non-click formats are never treated as CPC.
    if payments.intersection({'CPO', 'CPM'}) or objects.intersection({'SEARCH_PROMO', 'BANNER'}):
        return 'excluded_non_CPC_or_conflict'
    if payments == {'CPC'}:
        return 'CPC'
    return 'unverified'
