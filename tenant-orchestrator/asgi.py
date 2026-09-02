"""ASGI entry point. Kept trivial so the app factory stays testable."""

from app.main import create_app

app = create_app()
