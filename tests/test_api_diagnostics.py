import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from ozon_profit import api_diagnostics as diag
from diagnose_api import main


def fixture_database(directory):
    path = directory / 'profit.db'
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE secure_items(name TEXT PRIMARY KEY,value BLOB);
        CREATE TABLE product_links(ozon_sku TEXT,sku TEXT);
        CREATE TABLE operations(private_data TEXT);
        INSERT INTO operations VALUES('BUYER_PRIVATE_FINANCIAL_TRANSACTION');''')
    db.execute('INSERT INTO secure_items VALUES(?,?)',
               ('performance', b'DPAPI:encrypted-performance'))
    db.execute('INSERT INTO product_links VALUES(?,?)', ('12345', diag.ARTICLE))
    db.commit()
    db.close()
    (directory / 'seller.credentials').write_bytes(b'encrypted-seller')
    return path


def mock_dpapi(monkeypatch):
    from ozon_profit import credentials

    def decrypt(raw, decrypt=False):
        assert decrypt
        if raw == b'encrypted-seller':
            return json.dumps({'client_id': '987654321', 'api_key': 'SELLER_SECRET'}).encode()
        assert raw == b'encrypted-performance'
        return b'["PERF_CLIENT_SECRET", "PERF_SECRET"]'
    monkeypatch.setattr(credentials, '_transform', decrypt)


def test_readonly_credentials_match_rc3_without_touching_finance(tmp_path, monkeypatch):
    path = fixture_database(tmp_path)
    before = hashlib.sha256(path.read_bytes()).digest()
    mock_dpapi(monkeypatch)
    queries = []
    original = diag.open_readonly

    def traced(path):
        db = original(path)
        db.set_trace_callback(queries.append)
        return db
    monkeypatch.setattr(diag, 'open_readonly', traced)
    pairs, states, skus = diag.read_connections(tmp_path)
    assert pairs == {'seller': ('987654321', 'SELLER_SECRET'),
                     'performance': ('PERF_CLIENT_SECRET', 'PERF_SECRET')}
    assert states == {'seller': 'loaded', 'performance': 'loaded', 'database': 'opened_read_only'}
    assert skus == {'12345'}
    assert not any('operations' in query.lower() for query in queries)
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with diag.open_readonly(path) as db:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM operations')
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('CREATE TABLE forbidden(x)')


def test_missing_database_is_not_created(tmp_path):
    report = diag.run(tmp_path / 'missing', offline=True)
    assert not (tmp_path / 'missing').exists()
    assert report['connections']['database'] == 'missing'
    assert report['execution'] == 'offline_no_API_requests'


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.sleep = lambda seconds: None

    def request(self, service, method, path, headers, **kwargs):
        self.calls.append((service, method, path, kwargs))
        data = {
            '/v3/product/info/list': {'items': [{'offer_id': diag.ARTICLE, 'sku': 12345}]},
            '/v3/posting/fbo/list': {'postings': [{'buyer': 'PRIVATE_BUYER', 'price': 9999}]},
            '/v4/posting/fbs/list': {'postings': []},
            '/v4/product/info/stocks': {'items': [{'private': 'PRIVATE_STOCK'}]},
            '/v1/analytics/stocks': {'items': []},
            '/v1/analytics/product-queries/details': {
                'queries': [{'query': 'PRIVATE_QUERY', 'position': None, 'unique_search_users': 0}]},
            '/v1/analytics/data': {'result': {'data': [{'dimensions': [
                {'id': '12345', 'name': 'PRIVATE_PRODUCT'}, {'id': diag.START}], 'metrics': [0]}]}},
            '/api/client/token': {'access_token': 'PRIVATE_BEARER_TOKEN'},
            '/api/client/campaign': {'list': [
                {'id': '111', 'paymentType': 'CPC', 'title': 'PRIVATE_CAMPAIGN'},
                {'id': '222', 'paymentType': 'CPO'}, {'id': '333', 'paymentType': 'CPM'}]},
            '/api/client/statistics/json': {'UUID': 'aaaaaaaa-1111-2222-3333-444444444444'},
            '/api/client/statistics/aaaaaaaa-1111-2222-3333-444444444444': {
                'state': 'OK', 'link': 'https://attacker.test/private'},
            '/api/client/statistics/report': {'111': {'report': {'rows': [
                {'sku': '12345', 'date': diag.START, 'clicks': '0', 'toCart': 64,
                 'expense': '2059', 'orders': None, 'modelOrders': 2,
                 'buyer': 'PRIVATE_BUYER', 'api_key': 'SELLER_SECRET'}]}}},
        }[path]
        return {'http_status': 200, 'outcome': 'response_received',
                'subscription_requirement': 'not_established'}, data

    def close(self):
        self.closed = True


def test_full_flow_safe_report_CPC_only_no_combined_conversion(tmp_path, monkeypatch):
    fixture_database(tmp_path)
    mock_dpapi(monkeypatch)
    transport = FakeTransport()
    report = diag.run(tmp_path, transport=transport)
    text = json.dumps({key: value for key, value in report.items() if key != 'created_at'})
    for forbidden in ('PRIVATE_', 'SELLER_SECRET', 'PERF_SECRET', 'PERF_CLIENT_SECRET',
                      '987654321', '12345', 'attacker', 'aaaaaaaa', 'BUYER_PRIVATE'):
        assert forbidden not in text
    generated = [call for call in transport.calls if call[2] == '/api/client/statistics/json']
    assert len(generated) == 1
    assert generated[0][3]['json'] == {'campaigns': ['111'], 'dateFrom': diag.START,
                                       'dateTo': diag.END, 'groupBy': 'DATE'}
    sample = report['checks'][-1]['sample']
    assert sample['fields']['clicks']['numeric_rows'] == 1  # zero is data
    assert sample['fields']['orders']['numeric_rows'] == 0  # null is not zero
    assert sample['target_days_observed'] == 1
    assert not sample['totals_computed']
    assert report['owner_reference_not_api_result']['advertising_attributed_orders'] is None
    assert report['subscription'] == 'unknown_not_inferred_from_HTTP_status'
    assert transport.closed
    assert all('finance' not in call[2] for call in transport.calls)


class Response:
    def __init__(self, status=200, content=b'{}'):
        self.status_code, self.content = status, content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, size):
        yield self.content


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response
    def close(self):
        pass


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 302])
def test_HTTP_errors_never_prove_subscription_or_export_error_body(status):
    session = Session(Response(status, b'{"message":"Premium Plus PRIVATE_KEY BUYER"}'))
    outcome, data = diag.Transport(session).request('seller', 'POST',
        '/v1/analytics/data', {'Api-Key': 'PRIVATE_KEY'}, json={})
    assert data is None
    assert outcome['http_status'] == status
    assert outcome['subscription_requirement'] == 'not_established'
    assert 'PRIVATE' not in json.dumps(outcome)
    assert session.calls[0][1]['allow_redirects'] is False


def test_transport_rate_limit_and_forbidden_methods():
    sleeps = []
    transport = diag.Transport(Session(Response()), sleep=sleeps.append, clock=lambda: 100)
    for _ in range(2):
        transport.request('seller', 'POST', '/v1/analytics/data', {})
    assert sleeps == [61]
    for service, method, path in [('seller', 'POST', '/v1/finance/accrual/by-day'),
        ('performance', 'POST', '/api/client/campaign'),
        ('performance', 'GET', 'https://evil.test'), ('wrong', 'GET', '/foo')]:
        with pytest.raises(ValueError):
            transport.request(service, method, path, {})


@pytest.mark.parametrize('response, expected', [
    (Response(content=b'PRIVATE_INVALID_JSON'), 'invalid_json'),
    (requests.exceptions.SSLError('PRIVATE_KEY'), 'tls_error'),
    (requests.exceptions.Timeout('PRIVATE_KEY'), 'timeout'),
    (requests.exceptions.ProxyError('PRIVATE_KEY'), 'network_error'),
    (Response(content=b'x' * (16 * 1024 * 1024 + 1)), 'response_too_large')])
def test_transport_failures_redacted(response, expected):
    outcome, data = diag.Transport(Session(response)).request(
        'seller', 'POST', '/v3/product/info/list', {})
    assert data is None and outcome['outcome'] == expected
    assert 'PRIVATE' not in json.dumps(outcome)


def test_unknown_and_wrong_target_schema_are_not_zero():
    assert diag.performance_summary({'surprise': []}, {'123'})['schema'] == 'unrecognized'
    sample = diag.performance_summary({'rows': [{'sku': 123, 'clicks': 4}]}, {'123'})
    assert sample['schema'] == 'unrecognized'  # day missing
    sample = diag.analytics_summary({'result': {'data': [{'dimensions': [
        {'id': 'wrong'}, {'id': diag.START}], 'metrics': [0]}]}}, '123', 'hits_view')
    assert sample['schema'] == 'unrecognized'
    assert diag.numeric(None) is False
    assert diag.numeric('NaN') is False
    assert diag.numeric(True) is False


def test_pending_job_stops_next_export_and_ignores_arbitrary_link(tmp_path, monkeypatch):
    fixture_database(tmp_path)
    mock_dpapi(monkeypatch)
    transport = FakeTransport()
    request = transport.request

    def pending(service, method, path, headers, **kwargs):
        outcome, data = request(service, method, path, headers, **kwargs)
        if path == '/api/client/campaign':
            data['list'].append({'id': '444', 'paymentType': 'CPC'})
        if 'aaaaaaaa-' in path:
            data = {'state': 'IN_PROGRESS', 'link': 'https://evil.test'}
        return outcome, data
    transport.request = pending
    report = diag.run(tmp_path, transport=transport)
    assert report['checks'][-1]['outcome'] == 'pending_not_failed_no_more_exports_started'
    assert len([call for call in transport.calls if call[2] == '/api/client/statistics/json']) == 1


def test_cli_writes_report_outside_data_dir_and_never_overwrites(tmp_path):
    data = tmp_path / 'data'
    output = tmp_path / 'diagnostics.json'
    assert main(['--offline', '--data-dir', str(data), '--output', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['execution'] == 'offline_no_API_requests'
    before = output.read_bytes()
    assert main(['--offline', '--data-dir', str(data), '--output', str(output)]) == 2
    assert output.read_bytes() == before
    assert main(['--offline', '--data-dir', str(data), '--output', str(data / 'profit.db.json')]) == 2


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Requires real Windows DPAPI')
def test_real_windows_saved_connections(tmp_path):
    from ozon_profit import credentials
    fixture_database(tmp_path)
    credentials.save('123', 'synthetic-test-key', tmp_path / 'seller.credentials')
    with sqlite3.connect(tmp_path / 'profit.db') as db:
        db.execute('UPDATE secure_items SET value=? WHERE name=?',
                   (b'DPAPI:' + credentials._transform(b'["synthetic-client", "synthetic-secret"]'),
                    'performance'))
    pairs, states, _ = diag.read_connections(tmp_path)
    assert states['seller'] == states['performance'] == 'loaded'
    assert pairs['performance'] == ('synthetic-client', 'synthetic-secret')
