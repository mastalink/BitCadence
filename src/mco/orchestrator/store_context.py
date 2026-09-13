"""Backend boundaries shared by audit and leases, without a dependency cycle.

SQLite operations share a transaction on their LocalStore connection. PostgreSQL
atomicity belongs inside the RPC: a Python context cannot span HTTP requests.
"""
from contextlib import AbstractContextManager, nullcontext
from typing import Any


def is_postgres(db: Any) -> bool:
    return type(db).__module__.split('.')[0] in {'supabase', 'postgrest'}


def transaction(db: Any) -> AbstractContextManager:
    from mco.localstore import LocalStore
    return db.transaction() if isinstance(db, LocalStore) else nullcontext()
