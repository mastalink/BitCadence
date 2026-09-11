"""Bootstrap a DISPOSABLE test database twice and wait for PostgREST.

Requires BC_TEST_DATABASE_URL and BC_TEST_POSTGREST_URL. Never point this at
an existing production database: it installs the AWS bootstrap and migrations.
"""
import os
from pathlib import Path
import time
import urllib.request

import psycopg

from mco.migrations_runner import apply_postgres

root = Path(__file__).resolve().parents[1]
dsn = os.environ['BC_TEST_DATABASE_URL']
url = os.environ['BC_TEST_POSTGREST_URL']
base = (root/'infra/aws/gateway/migrations-overlay/000-base-schema.sql').read_text(encoding='utf-8')
for boot in range(2):
    with psycopg.connect(dsn) as conn:
        conn.execute(base)
    apply_postgres(dsn)
    print(f'Bootstrap pass {boot + 1} succeeded', flush=True)
with psycopg.connect(dsn, autocommit=True) as conn:
    conn.execute("NOTIFY pgrst, 'reload schema'")
for attempt in range(30):
    try:
        with urllib.request.urlopen(url+'/mco_store_identity?select=id',timeout=2) as response:
            if response.status == 200:
                print('PostgREST migration contract ready', flush=True)
                break
    except Exception:
        if attempt == 29: raise
        time.sleep(1)
