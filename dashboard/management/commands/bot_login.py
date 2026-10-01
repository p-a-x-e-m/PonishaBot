"""Blocking terminal login (opens Chrome): python manage.py bot_login"""
from django.core.management import BaseCommand


class Command(BaseCommand):
    help = "Open Chrome and complete ponisha.ir login, then save cookies"

    def handle(self, *args, **options):
        from ponishabot import botstate

        app = botstate.get_app()
        self.stdout.write("Opening login browser (headed Chrome)…")
        ok = app.auth.auth(interactive=True)
        self.stdout.write(self.style.SUCCESS("Login OK") if ok
                          else self.style.ERROR("Login failed"))
