from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.permissions import matrix_markdown


class Command(BaseCommand):
    help = "Write docs/PERMISSIONS.md from core/permissions.py (--check fails if it is stale)."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true")

    def handle(self, *args, check=False, **opts):
        path = Path(settings.BASE_DIR) / "docs" / "PERMISSIONS.md"
        content = matrix_markdown()
        if check:
            if not path.exists() or path.read_text() != content:
                raise CommandError("docs/PERMISSIONS.md is stale; run manage.py permission_matrix")
            return
        path.write_text(content)
        self.stdout.write(f"Wrote {path}")
