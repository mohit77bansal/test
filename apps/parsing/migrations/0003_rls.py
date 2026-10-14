from django.db import migrations

from apps.core.rls import EnableRLS


class Migration(migrations.Migration):
    dependencies = [("parsing", "0002_initial")]
    operations = [EnableRLS("files"), EnableRLS("parse_jobs")]
