from django.core.management.base import BaseCommand
from smart_campus.management.check_alerts import Command as AlertCommand

class Command(AlertCommand):
    help = "Shortcut: run the alert generation engine (same as check_alerts)"
