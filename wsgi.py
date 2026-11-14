"""WSGI entry point. Does not start the local reminder thread."""
from app import create_app

app = create_app()
