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
    monkeypatch.setattr(r,'_one',lambda slug,f,model,cancel,worker: dict(status='SUCCESS',structured_output=candidate(f['address']),usage={'total_tokens':123}))
    r.start(SLUG,1,'test-model')
    r.running[(SLUG,1)][0].join(5)
    state=r.snapshot(SLUG)
    assert state['state']=='completed' and state['candidate_count']==1 and state['tokens']==123
    saved=r.candidate(SLUG,ADDR)
    assert saved['verified'] is False and saved['status']=='candidate'
    assert e.snapshot(SLUG)['functions'][0]['c']=='void fn(void) {}'
    r.start(SLUG,1,'test-model')
    r.running[(SLUG,1)][0].join(5)
    assert r.snapshot(SLUG)['candidate_count']==2
    assert ReconstructionEngine(e).snapshot(SLUG)['candidate_count']==2


def test_failed_or_wrong_address_output_cannot_mark_success(engines,monkeypatch):
    e,r=engines
    monkeypatch.setattr(r,'_one',lambda *args:dict(status='SUCCESS',structured_output=candidate('wrong')))
    r.start(SLUG)
    r.running[(SLUG,1)][0].join(5)
    assert r.snapshot(SLUG)['state']=='error'
    assert not r.candidates(SLUG)
    assert e.snapshot(SLUG)['drafted']==2


def test_cancellation_retains_candidates_and_releases_function(engines,monkeypatch):
    _,r=engines
    def wait(slug,f,model,cancel,worker):
        cancel.wait(2)
        return {}
    monkeypatch.setattr(r,'_one',wait)
    r.start(SLUG)
    r.cancel(SLUG)
    r.running[(SLUG,1)][0].join(5)
    assert r.snapshot(SLUG)['state']=='cancelled'
    assert r.snapshot(SLUG)['current'] is None


def test_restart_marks_ai_job_interrupted(engines):
    e,r=engines
    write_json(r._path(SLUG),{'state':'reconstructing','current':{'address':ADDR},'events':[]})
    assert ReconstructionEngine(e).snapshot(SLUG)['state']=='interrupted'


def test_ai_limits_and_no_reprocessing(engines):
    _,r=engines
    for value in (True,0,101,'1'):
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


def expand_functions(e, count):
    rows = [dict(type='summary', total_functions=count)]
    rows += [dict(type='function', name=f'fn{i}', address=hex(0x7100001000+i*4),
                  relative_address=hex(i*4), size=4, status='drafted', assembly='ret', c='void fn(void) {}')
             for i in range(count)]
    (e.root / SLUG / 'functions.jsonl').write_text('\n'.join(map(json.dumps,rows))+'\n')


def test_five_workers_process_100_each_without_duplicate_claims(engines, monkeypatch):
    e,r=engines
    expand_functions(e,500)
    release=threading.Event()
    entered=[threading.Event() for _ in range(5)]
    seen=[]
    def one(slug,f,model,cancel,worker):
        entered[worker-1].set()
        assert release.wait(5)
        with r.lock:
            seen.append((worker,f['address'],model))
        return dict(status='SUCCESS',structured_output=candidate(f['address']),usage={'total_tokens':1})
    monkeypatch.setattr(r,'_one',one)
    try:
        for worker in range(1,6):
            r.start(SLUG,100,f'model-{worker}',worker=worker)
        assert all(event.wait(5) for event in entered)
        state=r.snapshot(SLUG)
        assert state['active_count']==5 and len({w['address'] for w in state['currents']})==5
        assert sum(len(claims) for claims in r.reserved.values())==500
        assert len(set.union(*r.reserved.values()))==500
        with pytest.raises(InputError,match='already active'):
            r.start(SLUG,100,worker=1)
        with pytest.raises(InputError,match='all five'):
            r.start('b'*64,100,worker=1)
    finally:
        release.set()
        for thread,_ in list(r.running.values()):thread.join(15)
    state=r.snapshot(SLUG)
    assert state['candidate_count']==500 and state['completed']==500 and state['tokens']==500
    assert state['active_count']==0 and not r.reserved
    assert len(seen)==len({addr for _,addr,_ in seen})==500
    assert all(model==f'model-{worker}' for worker,_,model in seen)
    assert all(w['state']=='completed' and w['completed']==100 for w in state['workers'])
    assert all(r.candidate(SLUG,addr)['verified'] is False for _,addr,_ in seen)
    assert all(row['c']=='void fn(void) {}' for row in e.snapshot(SLUG)['functions'])


def test_cancel_one_worker_leaves_others_running_and_releases_claim(engines,monkeypatch):
    _,r=engines
    entered={n:threading.Event() for n in (1,2)}
    def one(slug,f,model,cancel,worker):
        entered[worker].set();cancel.wait(5);return {}
    monkeypatch.setattr(r,'_one',one)
    try:
        r.start(SLUG,worker=1);r.start(SLUG,worker=2)
        assert all(event.wait(3) for event in entered.values())
        first=r._worker(SLUG,1)['current']['address']
        r.cancel(SLUG,1);r.running[(SLUG,1)][0].join(3)
        assert r._worker(SLUG,1)['state']=='cancelled'
        assert r._worker(SLUG,2)['state']=='reconstructing'
        assert not r.running[(SLUG,2)][1].is_set()
        assert (SLUG,1) not in r.reserved
        r.start(SLUG,address=first,worker=1)
    finally:
        r.cancel(SLUG)
        r.stop()
    assert r.snapshot(SLUG)['active_count']==0 and not r.reserved


def test_worker_failure_releases_claims_and_keeps_other_results(engines,monkeypatch):
    _,r=engines
    def one(slug,f,model,cancel,worker):
        if worker==1:raise InputError('quota reached')
        return dict(status='SUCCESS',structured_output=candidate(f['address']))
    monkeypatch.setattr(r,'_one',one)
    r.start(SLUG,worker=1);r.running[(SLUG,1)][0].join(3)
    assert r._worker(SLUG,1)['state']=='error' and not r.reserved
    r.start(SLUG,2,worker=2);r.running[(SLUG,2)][0].join(3)
    assert r.snapshot(SLUG)['candidate_count']==2
    assert r._worker(SLUG,2)['state']=='completed'


def test_all_worker_files_recover_without_automatic_restart(engines):
    e,r=engines
    for n in range(1,6):
        write_json(r._path(SLUG,n),{'state':'reconstructing','current':{'address':ADDR},'events':[]})
    recovered=ReconstructionEngine(e)
    assert all(w['state']=='interrupted' and w['current'] is None for w in recovered.snapshot(SLUG)['workers'])
    assert not recovered.running and not recovered.reserved


def test_worker_validation(engines):
    _,r=engines
    for n in (True,0,6,'1',None):
        with pytest.raises(InputError):r.start(SLUG,worker=n)
    for n in (True,0,6,'1'):
        with pytest.raises(InputError):r.cancel(SLUG,n)


def test_parallel_cli_processes_have_separate_workdirs_and_selected_models(engines,monkeypatch):
    import subprocess
    import sys
    import decompnet.analysis.reconstruct as module
    e,r=engines
    expand_functions(e,5)
    real=subprocess.Popen;calls=[]
    script='''import json,sys,time
payload=json.loads(sys.stdin.readline())
context=json.loads(payload['message']['content'].split('\\n\\n',1)[1])
f=context['function']
time.sleep(.2)
candidate=dict(address=f['address'],suggested_name='noop',source='void noop(void) {}',summary='fixture',evidence=['RET'],uncertainties=[])
print(json.dumps({'event':'result','result':{'status':'SUCCESS','structured_output':candidate}}))
'''
    def launch(cmd,**kwargs):
        calls.append((cmd,kwargs['cwd']))
        return real([sys.executable,'-c',script],**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    for worker in range(1,6):r.start(SLUG,1,f'model-{worker}',worker=worker)
    for thread,_ in list(r.running.values()):thread.join(5)
    assert r.snapshot(SLUG)['candidate_count']==5
    assert len({cwd for _,cwd in calls})==5
    assert {cmd[cmd.index('--model')+1] for cmd,_ in calls}=={f'model-{n}' for n in range(1,6)}
    assert all('--sandbox' in cmd and '--dangerously-skip-permissions' not in cmd for cmd,_ in calls)
