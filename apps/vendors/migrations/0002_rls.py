from django.db import migrations

from apps.core.rls import EnableRLS


class Migration(migrations.Migration):
    dependencies = [("vendors", "0001_initial")]
    operations = [
        EnableRLS("vendors", column="id"),
        EnableRLS("vendor_users"),
        EnableRLS("vendor_gstins"),
        EnableRLS("vendor_bank_accounts"),
        EnableRLS("vendor_documents"),
    ]
