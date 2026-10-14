from django.db import migrations

from apps.core.rls import EnableRLS


class Migration(migrations.Migration):
    dependencies = [("invoices", "0002_initial")]
    operations = [
        EnableRLS("invoices"),
        EnableRLS("invoice_line_items"),
        EnableRLS("approval_actions"),
        EnableRLS("invoice_messages"),
        EnableRLS("payments"),
        EnableRLS("purchase_orders"),
    ]
