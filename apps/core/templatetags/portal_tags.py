from decimal import Decimal, InvalidOperation

from django import template

from apps.core.permissions import has_perm

register = template.Library()


@register.filter
def can(user, code):
    return has_perm(user, code)


@register.filter
def inr(value):
    """Format a number in Indian grouping: 1500000 -> ₹15,00,000.00"""
    if value is None or value == "":
        return ""
    try:
        d = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return value
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac}"


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    return mapping.get(key)


STATUS_COLOURS = {
    "ACTIVE": "green", "APPROVED": "green", "PAID": "green", "DONE": "green",
    "REJECTED": "red", "BLOCKED": "red", "FAILED": "red",
    "QUERY_RAISED": "amber", "PARTLY_PAID": "amber", "PENDING": "amber", "UPLOADED": "amber",
}


@register.inclusion_tag("partials/status_badge.html")
def status_badge(status, label=None):
    return {"status": status, "label": label or str(status).replace("_", " ").title(), "colour": STATUS_COLOURS.get(status, "blue")}
