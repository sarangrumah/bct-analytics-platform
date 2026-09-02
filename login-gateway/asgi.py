"""ASGI entry point. Kept separate from app.main so the factory can be exercised in tests with
an explicit Settings object rather than through the environment."""

from app.main import create_app

app = create_app()
