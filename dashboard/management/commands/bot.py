"""Run the web GUI: python manage.py bot [--host H] [--port P]

Always uses --noreload so the monitor thread and Chrome survive reloads.
The bot owns a single-process singleton (monitor thread + Selenium), so this
must stay ONE process — never run it behind multiple workers.
"""
import os
import sys

from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Start the ponishabot web dashboard (default 127.0.0.1:8000)"

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("BOT_HOST", "127.0.0.1"),
                            help="bind address (use 0.0.0.0 to expose on a VPS)")
        parser.add_argument("--port", default=int(os.environ.get("BOT_PORT", "8000")),
                            type=int, help="bind port")

    def handle(self, *args, **options):
        # Windows console often cp1252 — ConfigLoader prints Persian skills
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError, OSError):
                pass
        host = options["host"]
        port = options["port"]
        addr = f"{host}:{port}"
        self.stdout.write(f"ponishabot web GUI → http://{addr}/")
        self.stdout.write("Always --noreload (monitor thread lives in this process).")
        if host not in ("127.0.0.1", "localhost"):
            self.stdout.write(self.style.WARNING(
                "WARNING: dashboard has no authentication — put nginx/auth or "
                "an SSH tunnel in front before exposing it."))
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING(
                "DEBUG is off — serving /static/ via --insecure. Without it the "
                "dashboard loads with no CSS/JS and every button does nothing."))
        # insecure=True so /static/ is served even when DJANGO_DEBUG=0; without
        # it the page renders unstyled and poll.js never loads, which silently
        # kills every button (they only get handlers from that file).
        call_command("runserver", addr, use_reloader=False, insecure=True)
