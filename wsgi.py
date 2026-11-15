"""WSGI entry point. Does not start the local reminder thread."""
from ruang_pangkas.web import create_app

app = create_app()
