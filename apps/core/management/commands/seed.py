from django.core.management.base import BaseCommand

from apps.core.seed import seed_demo, seed_reference


class Command(BaseCommand):
    help = "Seed roles, document types, email templates, a sample cost centre and approval matrix (--demo adds users/vendors)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also create demo users (password Passw0rd!234) and vendors A/B")

    def handle(self, *args, demo=False, **opts):
        seed_reference()
        if demo:
            seed_demo()
        self.stdout.write(self.style.SUCCESS("Seed complete" + (" (with demo data)" if demo else "")))
