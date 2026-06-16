#!/usr/bin/env python
import os
import sys
import subprocess

def main():
    # Auto-start MQTT listener when running the dev server
    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "mqtt_listener.py")],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smart_campus.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install it with: pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()

