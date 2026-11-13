"""Canceling a destructive-action prompt must never submit a request."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE=(Path(__file__).resolve().parents[1]/'src/mco/dashboard.py').read_text(encoding='utf-8')

@pytest.mark.parametrize('function,end,call',[
    ('abortActiveRun','openLiveLook',"await abortActiveRun('test-run')"),
    ('bulkAction','toggleAutonomyPause',"await bulkAction('cancel')"),
    ('decide','retryJob',"await decide('job-1','reject')"),
])
@pytest.mark.parametrize('answer,expected',[(None,0),('',1),('Owner requested',1)])
def test_prompt_cancel_does_not_call_api(function,end,call,answer,expected):
    node=shutil.which('node')
    if not node:pytest.skip('Node is needed to exercise dashboard JavaScript')
    start=SOURCE.index('async function '+function+'(')
    finish=SOURCE.index('async function '+end+'(',start)
    function_source=SOURCE[start:finish]
    # The bulk source block includes helper functions; none have side effects until invoked.
    code="""let calls=[];
const selectedJobs=new Set(['job-1']);
const prompt=()=>ANSWER;const alert=()=>{};
const api=async(...args)=>{calls.push(args);return {succeeded:[],failed:{}}};
const clearSelection=()=>{};const refreshOps=()=>{};const openLiveLook=()=>{};
""".replace('ANSWER',json.dumps(answer))+function_source+'\n(async()=>{'+call+';process.stdout.write(JSON.stringify(calls));})().catch(e=>{console.error(e);process.exit(1)});'
    result=subprocess.run([node,'-e',code],capture_output=True,text=True,check=True)
    assert len(json.loads(result.stdout))==expected
