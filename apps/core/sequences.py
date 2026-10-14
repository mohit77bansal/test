from django.db import connection


def next_value(name: str) -> int:
    """Postgres sequence-backed counter (gap-tolerant, concurrency-safe)."""
    if not name.replace("_", "").isalnum():
        raise ValueError(name)
    with connection.cursor() as cur:
        cur.execute(f"CREATE SEQUENCE IF NOT EXISTS seq_{name}")
        cur.execute("select nextval(%s)", [f"seq_{name}"])
        return cur.fetchone()[0]


def next_code(name: str, model, field: str, fmt: str) -> str:
    """Next sequence value formatted with `fmt`, skipping codes already used (e.g. by seed data)."""
    while True:
        code = fmt.format(next_value(name))
        if not model.objects.filter(**{field: code}).exists():
            return code
