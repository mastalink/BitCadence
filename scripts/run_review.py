"""Run a separate local review instance without changing ~/.mco or the fleet.

    python scripts/run_review.py --port 18890

The token and database persist in .codex/review. The bundled checksum worker
executes real, deterministic jobs; AI roles require their own configured workers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18890)
    args=parser.parse_args()
    runtime=ROOT/'.codex'/'review'
    runtime.mkdir(parents=True,exist_ok=True)
    # This process is deliberately isolated from the operator's normal config.
    for key in list(os.environ):
        if key.startswith(('MCO_','NTFY_','SUPABASE_')):
            os.environ.pop(key)
    from mco import config, security
    from mco.localstore import LocalStore, seed_local_operator
    settings=runtime/'review.env'
    if not settings.exists():
        settings.write_text('MCO_LOCAL_TOKEN='+secrets.token_urlsafe(32)+'\nNTFY_TOPIC=\nMCO_DRUMLINE_DISTILL=true\n',encoding='utf-8')
    config._config_manager=config.ConfigManager(settings,runtime/'secrets.enc')
    token=config.get_config().get('MCO_LOCAL_TOKEN')
    from mco.orchestrator import routes
    store=LocalStore(runtime/'review.db')
    seed_local_operator(store,token)
    routes._db_client=store
    worker_token=hashlib.sha256((token+':checksum').encode()).hexdigest()
    store.table('agent_registry').upsert({'instance_id':'review-checksum','role':'checksum',
        'status':'offline','auth_token_hash':hashlib.sha256(worker_token.encode()).hexdigest()}).execute()
    from mco.sdk import BitCadenceAgent
    worker=BitCadenceAgent('checksum','review-checksum',token=worker_token,
                           gateway=f'http://127.0.0.1:{args.port}',poll_interval=2)
    @worker.handler
    def calculate(job,prompt):
        worker.checkpoint()
        raw=(job.get('input_payload') or {}).get('prompt') or job.get('description') or job['title']
        return 'SHA-256: '+hashlib.sha256(raw.encode()).hexdigest()
    def run_worker():
        time.sleep(2)
        worker.run()
    threading.Thread(target=run_worker,daemon=True).start()
    (runtime/'connection.json').write_text(json.dumps({'url':f'http://127.0.0.1:{args.port}',
        'token':token},indent=2),encoding='utf-8')
    (runtime/'pid').write_text(str(os.getpid()),encoding='ascii')
    from mco.cli import create_app
    import uvicorn
    print(f'Review console: http://127.0.0.1:{args.port}/console',flush=True)
    print(f'Connection details: {runtime / "connection.json"}',flush=True)
    uvicorn.run(create_app(),host='127.0.0.1',port=args.port)

if __name__=='__main__':main()
