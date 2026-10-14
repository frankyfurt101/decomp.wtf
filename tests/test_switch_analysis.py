"""Auth, malformed-input, persistence, and job-failure regressions. No game data."""
import json
import struct
import sys
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from decompnet.analysis.inputs import InputError, inspect
from decompnet.analysis.engine import AnalysisEngine, write_json
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build


def nso_bytes():
    h = bytearray(0x10c)
    h[:4] = b'NSO0'
    for i in range(3):
        struct.pack_into('<IIII', h, 0x10 + i * 16, 0x100 + i * 4, i * 0x1000, 4, 0)
    h[0x100:] = bytes.fromhex('c0035fd6') * 3  # original minimal RET instructions
    return h


@pytest.fixture
def nso(tmp_path):
    p = tmp_path / 'main'
    p.write_bytes(nso_bytes())
    return p


def test_nso_identity_and_exefs_directory(nso):
    a = inspect(nso)
    assert a == inspect(nso.parent)
    assert a['format'] == 'NSO' and a['size'] == 0x10c
    assert a['segments'][2]['address'] == '0x2000'


@pytest.mark.parametrize('offset,value', [(0x10, 0xff), (0x18, 0xffffffff), (0x24, 0), (0x30, 0x100), (0x4, 1), (0xc, 0x80)])
def test_bad_nso_bounds_flags_and_overlap(nso, offset, value):
    data = nso_bytes()
    struct.pack_into('<I', data, offset, value)
    nso.write_bytes(data)
    with pytest.raises(InputError):
        inspect(nso)


@pytest.mark.parametrize('data,message', [(b'NSO0', 'truncated'), (b'PFS0', 'ExeFS'), (b'not a game', 'extracted'), (b'\x7fELF', 'malformed ELF')])
def test_bad_input_is_actionable(nso, data, message):
    nso.write_bytes(data)
    with pytest.raises(InputError, match=message):
        inspect(nso)


def elf_bytes(address=0x7100000000):
    h = bytearray(0x104)
    h[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', h, 16, 2, 183, 1, address, 64, 0, 0, 64, 56, 1, 64, 0, 0)
    struct.pack_into('<IIQQQQQQ', h, 64, 1, 5, 0x100, address, address, 4, 4, 4)
    h[0x100:] = bytes.fromhex('c0035fd6')
    return h


def test_elf_preserves_64_bit_addresses(nso):
    nso.write_bytes(elf_bytes())
    a = inspect(nso)
    assert a['entry'] == a['segments'][0]['address'] == '0x7100000000'
    data = elf_bytes((1 << 64) - 2)
    nso.write_bytes(data)
    with pytest.raises(InputError, match='virtual address'):
        inspect(nso)


def test_elf_wrong_architecture(nso):
    data = elf_bytes()
    struct.pack_into('<H', data, 18, 62)
    nso.write_bytes(data)
    with pytest.raises(InputError, match='AArch64'):
        inspect(nso)


def test_persistence_duplicates_and_interrupted_recovery(tmp_path, nso):
    root = tmp_path / 'analysis'
    e = AnalysisEngine(root)
    p = e.import_file(str(nso), '<script>My module</script>')
    assert p['verified_matches'] == 0 and not p['capabilities']['can_verify']
    assert not p['capabilities']['can_export_game']
    assert e.import_file(str(nso))['slug'] == p['slug']
    e.update(p['slug'], state='analyzing')
    recovered = AnalysisEngine(root).snapshot(p['slug'])
    assert recovered['state'] == 'interrupted'
    with pytest.raises(InputError):
        e.directory('../escape')


def test_partial_export_and_lightweight_snapshot(tmp_path, nso):
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    rows = [dict(type='summary', total_functions=2), dict(type='function', name='start', address='0x7100000000', relative_address='0x0', size=4, status='drafted', c='return;', assembly='ret')]
    (e.directory(slug) / 'functions.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n{"type":')
    p = e.snapshot(slug, details=False)
    assert p['drafted'] == 1 and not p['export_complete']
    assert 'c' not in p['functions'][0]


def test_failed_retry_cannot_reuse_old_success(tmp_path, nso, monkeypatch):
    import decompnet.analysis.engine as engine
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    (e.directory(slug) / 'functions.jsonl').write_text('{"type":"complete"}\n')
    real = engine.subprocess.Popen
    monkeypatch.setattr(engine.subprocess, 'Popen', lambda *a, **kw: real([sys.executable, '-c', 'pass'], **kw))
    e._run(slug, 1, threading.Event())
    p = e.snapshot(slug)
    assert p['state'] == 'error' and not p['export_complete']
    assert (e.directory(slug) / 'previous-functions.jsonl').exists()


def test_cancel_stops_worker_and_retains_state(tmp_path, nso, monkeypatch):
    import decompnet.analysis.engine as engine
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    real = engine.subprocess.Popen
    children = []
    def launch(*args, **kw):
        p = real([sys.executable, '-c', 'import time; time.sleep(30)'], **kw)
        children.append(p)
        return p
    monkeypatch.setattr(engine.subprocess, 'Popen', launch)
    cancel = threading.Event()
    cancel.set()
    e._run(slug, 1, cancel)
    assert children[0].poll() is not None
    assert e.snapshot(slug)['state'] == 'cancelled'


def test_routes_share_auth_and_do_not_enter_matching_database(tmp_path, nso):
    home = tmp_path / 'app'
    state = home / '.decomp'
    state.mkdir(parents=True)
    net = RomNetwork(state / 'decomp.db', home)
    mcp, _, security = build(net, state, 6499, 'test-only-secret')
    app = mcp.streamable_http_app(transport_security=security, host='127.0.0.1')
    client = TestClient(app, base_url='http://127.0.0.1:6499')
    for route in ('/switch', '/api/switch/projects', '/api/switch/project?id=x', '/api/switch/map?id=x', '/api/switch/agent/config', '/api/switch/function?id=x'):
        assert client.get(route).status_code == 401
    client.cookies.set('decomp_live', 'test-only-secret')
    redirect = client.get('/?k=test-only-secret&view=switch', follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == '/switch'
    redirect = client.get('/?k=test-only-secret&view=https://evil.example', follow_redirects=False)
    assert redirect.headers['location'] == '/'
    assert client.get('/switch', follow_redirects=False).headers['location'] == '/#/switch'
    assert client.get('/switch').status_code == 200
    payload = {'path': str(nso), 'confirmed': True}
    assert client.post('/api/switch/import', json=payload).status_code == 403
    for route in ('/api/switch/agent/start', '/api/switch/agent/stop'):
        assert client.post(route, json={}).status_code == 403
    h = {'X-Decomp': '1'}
    assert client.post('/api/switch/import', json={'path': str(nso)}, headers=h).status_code == 400
    r = client.post('/api/switch/import', json=payload, headers=h)
    assert r.status_code == 200, r.text
    slug = r.json()['slug']
    assert client.get('/api/switch/project', params={'id': slug}).json()['state'] == 'imported'
    assert client.get('/api/switch/map', params={'id': slug}).json() == []
    assert client.get('/api/state').json()['projects'] == []
    for route in ('/api/switch/agent/start', '/api/switch/agent/stop'):
        assert client.post(route, json=[], headers=h).status_code == 400
    assert client.post('/api/switch/agent/start', json={'id':slug,'limit':21}, headers=h).status_code == 400
    for invalid in (True, 0, 501, '64'):
        assert client.post('/api/switch/start', json={'id': slug, 'limit': invalid}, headers=h).status_code == 400
    assert client.post('/api/switch/cancel', json=[], headers=h).status_code == 400
    evil = TestClient(app, base_url='http://evil.example:6499')
    evil.cookies.set('decomp_live', 'test-only-secret')
    assert evil.get('/api/switch/projects').status_code == 403


def test_live_events_retain_earlier_drafts_and_clear_current(tmp_path, nso):
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    d = e.directory(slug)
    old = dict(type='function', name='old', address='0x7100000000', relative_address='0x0', size=4, status='drafted', c='return;', assembly='ret')
    (d / 'base-functions.jsonl').write_text(json.dumps(old) + '\n')
    start = dict(type='started', name='next', address='0x7100000004', time=123)
    (d / 'functions.jsonl').write_text(json.dumps(start) + '\n')
    e.update(slug, state='drafting')
    p = e.snapshot(slug)
    assert p['drafted'] == 1 and p['current_function'] == start
    assert not p['export_complete']
    new = old | dict(name='next', address=start['address'], relative_address='0x4', time=124)
    with (d / 'functions.jsonl').open('a') as f:
        f.write(json.dumps(new) + '\n' + json.dumps({'type': 'complete'}) + '\n')
    p = e.snapshot(slug)
    assert p['drafted'] == 2 and p['current_function'] is None and p['export_complete']
    assert [r['type'] for r in p['events']] == ['started', 'function']
    assert [r['time'] for r in p['events']] == [123, 124]


def test_next_batch_preserves_results_and_passes_offset(tmp_path, nso, monkeypatch):
    import decompnet.analysis.engine as engine
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    d = e.directory(slug)
    old = dict(type='function', name='old', address='0x7100000000', relative_address='0x0', size=4, status='drafted', c='return;', assembly='ret')
    (d / 'functions.jsonl').write_text(json.dumps({'type':'summary','total_functions':10})+'\n'+json.dumps(old)+'\n'+json.dumps({'type':'complete'})+'\n')
    real = engine.subprocess.Popen
    commands = []
    def launch(argv, **kw):
        commands.append(argv)
        return real([sys.executable, '-c', 'pass'], **kw)
    monkeypatch.setattr(engine.subprocess, 'Popen', launch)
    e._run(slug, 5, threading.Event())
    assert commands[0][-2:] == ['5','1']
    p = e.snapshot(slug)
    assert p['state'] == 'error' and p['drafted'] == 1
    assert not p['export_complete'] and p['summary']['total_functions'] == 10


def test_catalog_contains_undrafted_functions(tmp_path, nso):
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    data = [{'name':'future','address':'0x7100001000','size':4}]
    (e.directory(slug) / 'catalog.json').write_text(json.dumps(data))
    assert e.catalog(slug) == data
    assert e.snapshot(slug)['catalog_version'] > 0
    assert e.snapshot(slug)['exported'] == 0


def test_lightweight_record_cache_refreshes_after_append(tmp_path, nso):
    e = AnalysisEngine(tmp_path / 'analysis')
    slug = e.import_file(str(nso))['slug']
    path=e.directory(slug)/'functions.jsonl'
    row=dict(type='function',name='f',address='0x7100000000',relative_address='0x0',size=4,status='drafted',c='return;',assembly='ret')
    path.write_text(json.dumps(row)+'\n')
    first=e.snapshot(slug,False)
    assert first['drafted']==1 and 'c' not in first['functions'][0]
    assert e.snapshot(slug)['functions'][0]['c']=='return;'
    with path.open('a') as f:f.write(json.dumps(row|dict(address='0x7100000004'))+'\n')
    assert e.snapshot(slug,False)['drafted']==2
