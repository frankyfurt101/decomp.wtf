import json
import threading
import time

import pytest

from decompnet.analysis.engine import AnalysisEngine, write_json
from decompnet.analysis.inputs import InputError
from decompnet.analysis.reconstruct import ReconstructionEngine, validate_candidate

SLUG = 'a' * 64
ADDR = '0x7100001000'


@pytest.fixture
def engines(tmp_path, monkeypatch):
    e = AnalysisEngine(tmp_path)
    d = tmp_path / SLUG
    d.mkdir()
    write_json(d / 'project.json', {'state':'completed', 'slug':SLUG, 'updated':0})
    rows = [dict(type='summary',total_functions=2)]
    for i in range(2):
        rows.append(dict(type='function',name=f'fn{i}',address=f'0x710000100{i}',relative_address=hex(i),size=4,
                         status='drafted',assembly='ret',c='void fn(void) {}'))
    (d/'functions.jsonl').write_text('\n'.join(map(json.dumps,rows))+'\n')
    r = ReconstructionEngine(e)
    monkeypatch.setattr('decompnet.analysis.reconstruct.agents._which',lambda _: '/test/agy')
    return e,r


def candidate(address=ADDR):
    return dict(address=address,suggested_name='return_noop',source='void return_noop(void) {}',
                summary='Returns without side effects.',evidence=['RET at entry'],uncertainties=['Calling context unknown'])


def test_candidate_identity_and_unverified_boundary():
    with pytest.raises(InputError,match='different function'):
        validate_candidate(candidate('wrong'),ADDR)
    data=validate_candidate(candidate()|dict(verified=True,status='matched'),ADDR)
    assert 'verified' not in data and 'status' not in data
    with pytest.raises(InputError):
        validate_candidate(candidate()|dict(source=''),ADDR)


def test_antigravity_persists_candidate_without_overwriting_ghidra(engines,monkeypatch):
    e,r=engines
    monkeypatch.setattr(r,'_one',lambda slug,f,model,cancel: dict(status='SUCCESS',structured_output=candidate(f['address']),usage={'total_tokens':123}))
    r.start(SLUG,1,'test-model')
    r.running[SLUG][0].join(5)
    state=r.snapshot(SLUG)
    assert state['state']=='completed' and state['candidate_count']==1 and state['tokens']==123
    saved=r.candidate(SLUG,ADDR)
    assert saved['verified'] is False and saved['status']=='candidate'
    assert e.snapshot(SLUG)['functions'][0]['c']=='void fn(void) {}'
    r.start(SLUG,1,'test-model')
    r.running[SLUG][0].join(5)
    assert r.snapshot(SLUG)['candidate_count']==2
    assert ReconstructionEngine(e).snapshot(SLUG)['candidate_count']==2


def test_failed_or_wrong_address_output_cannot_mark_success(engines,monkeypatch):
    e,r=engines
    monkeypatch.setattr(r,'_one',lambda *args:dict(status='SUCCESS',structured_output=candidate('wrong')))
    r.start(SLUG)
    r.running[SLUG][0].join(5)
    assert r.snapshot(SLUG)['state']=='error'
    assert not r.candidates(SLUG)
    assert e.snapshot(SLUG)['drafted']==2


def test_cancellation_retains_candidates_and_releases_function(engines,monkeypatch):
    _,r=engines
    def wait(slug,f,model,cancel):
        cancel.wait(2)
        return {}
    monkeypatch.setattr(r,'_one',wait)
    r.start(SLUG)
    r.cancel(SLUG)
    r.running[SLUG][0].join(5)
    assert r.snapshot(SLUG)['state']=='cancelled'
    assert r.snapshot(SLUG)['current'] is None


def test_restart_marks_ai_job_interrupted(engines):
    e,r=engines
    write_json(r._path(SLUG),{'state':'reconstructing','current':{'address':ADDR},'events':[]})
    assert ReconstructionEngine(e).snapshot(SLUG)['state']=='interrupted'


def test_ai_limits_and_no_reprocessing(engines):
    _,r=engines
    for value in (True,0,21,'1'):
        with pytest.raises(InputError):r.start(SLUG,value)
    with pytest.raises(InputError):r.start(SLUG,address='missing')


def test_continuous_runs_past_checkpoint_and_stops_at_inventory(engines,monkeypatch):
    e,_=engines
    state={'exported':0,'state':'completed','functions':[]}
    monkeypatch.setattr(e,'snapshot',lambda *args:dict(state))
    monkeypatch.setattr(e,'catalog',lambda slug:[{'address':str(i)} for i in range(3)])
    calls=[]
    def one(slug,limit,cancel):
        calls.append(limit);state['exported']+=1;state['functions'].append({'address':str(len(calls)-1)})
    monkeypatch.setattr(e,'_run',one)
    e._run_batches(SLUG,1,threading.Event(),True)
    assert len(calls)==3
    assert e.metadata(SLUG)['continuous'] is False


def test_continuous_stops_if_checkpoint_makes_no_progress(engines,monkeypatch):
    e,_=engines
    monkeypatch.setattr(e,'_run',lambda *args:None)
    e._run_batches(SLUG,1,threading.Event(),True)
    assert e.metadata(SLUG)['continuous'] is False


def test_cancel_terminates_actual_cli_process(engines,monkeypatch):
    import subprocess
    import sys
    import decompnet.analysis.reconstruct as module
    _,r=engines
    real=subprocess.Popen
    children=[]
    def launch(*args,**kwargs):
        proc=real([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    cancel=threading.Event();cancel.set()
    result=r._one(SLUG,{'name':'f','address':ADDR},'',cancel)
    assert result=={} and children[0].poll() is not None
