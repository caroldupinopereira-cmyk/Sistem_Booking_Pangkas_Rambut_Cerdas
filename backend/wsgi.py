"""WSGI entry point. Does not start the local reminder thread."""
from backend.web import create_app

app = create_app()
