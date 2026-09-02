"""ASGI entry point. Separate from app.main so the factory can be exercised in tests with explicit
dependencies rather than through the environment."""

from app.main import create_app

app = create_app()
