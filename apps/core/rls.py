"""Postgres row-level security helpers (see docs/ARCHITECTURE.md §3)."""
from contextlib import contextmanager

from django.db import DatabaseError, connection, migrations

POLICY_SQL = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS vendor_isolation ON {table};
CREATE POLICY vendor_isolation ON {table}
  USING (current_setting('app.is_internal', true) = 'on'
         OR {column}::text = current_setting('app.vendor_id', true))
  WITH CHECK (current_setting('app.is_internal', true) = 'on'
         OR {column}::text = current_setting('app.vendor_id', true));
"""

REVERSE_SQL = """
DROP POLICY IF EXISTS vendor_isolation ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def EnableRLS(table, column="vendor_id"):
    return migrations.RunSQL(
        POLICY_SQL.format(table=table, column=column), REVERSE_SQL.format(table=table)
    )


def _get():
    with connection.cursor() as cur:
        cur.execute(
            "select current_setting('app.is_internal', true), current_setting('app.vendor_id', true)"
        )
        return cur.fetchone()


def _set(is_internal, vendor_id):
    with connection.cursor() as cur:
        cur.execute(
            "select set_config('app.is_internal', %s, true), set_config('app.vendor_id', %s, true)",
            [is_internal or "off", vendor_id or ""],
        )


@contextmanager
def db_context(*, internal, vendor_id=None):
    """Set the RLS context for the current transaction (SET LOCAL) and restore it after.

    Must be called inside a transaction.
    """
    previous = _get()
    _set("on" if internal else "off", str(vendor_id) if vendor_id else "")
    try:
        yield
    except BaseException:
        # Restore if the transaction is still usable; if it is aborted, the rollback reverts SET LOCAL anyway.
        try:
            _set(*previous)
        except DatabaseError:
            pass
        raise
    _set(*previous)


def internal_context():
    """Escape hatch for pre-auth flows (e.g. invite token lookup). Use sparingly."""
    return db_context(internal=True)
