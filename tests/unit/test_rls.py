"""Postgres RLS is the backstop: even raw SQL in a vendor context sees only that vendor's rows."""
import pytest
from django.db import connection, transaction

from apps.core.rls import db_context


def _count(table):
    with connection.cursor() as cur:
        cur.execute(f"select count(*) from {table}")
        return cur.fetchone()[0]


def test_vendor_context_hides_other_vendors(seed, make_invoice):
    a, b = seed["vendor_a"], seed["vendor_b"]
    make_invoice(a, total=50000)
    make_invoice(b, total=60000)
    with db_context(internal=False, vendor_id=a.pk):
        assert _count("vendors") == 1
        assert _count("invoices") == 1
        assert _count("files") == 1
        with connection.cursor() as cur:
            cur.execute("select count(*) from invoices where vendor_id = %s", [b.pk])
            assert cur.fetchone()[0] == 0
    with db_context(internal=False, vendor_id=None):
        assert _count("vendors") == 0 and _count("invoices") == 0
    assert _count("invoices") == 2  # internal session default


def test_vendor_context_cannot_write_other_vendor_rows(seed):
    a, b = seed["vendor_a"], seed["vendor_b"]
    with pytest.raises(Exception), transaction.atomic(), db_context(internal=False, vendor_id=a.pk):
        with connection.cursor() as cur:
            cur.execute(
                "insert into vendor_gstins (id, created_at, updated_at, vendor_id, gstin, state_code, address, is_primary) "
                "values (gen_random_uuid(), now(), now(), %s, '29AAACB5678B1Z0', '29', '', false)",
                [b.pk],
            )


def test_app_role_is_not_superuser(db):
    with connection.cursor() as cur:
        cur.execute("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
        assert cur.fetchone() == (False, False)
