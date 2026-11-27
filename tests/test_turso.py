"""Run the business suite through the real libSQL driver on temporary local files.

This verifies the adapter, not remote transport or live Turso credentials.
"""
import unittest
from contextlib import ExitStack
from unittest.mock import patch
try:
    import libsql
except ImportError:
    libsql = None

from backend.database import RemoteConnection, TursoDatabase
from test_app import BookingTests as BaseBookingTests


@unittest.skipIf(libsql is None, "Install requirements.txt to test the Turso adapter")
class TursoAdapterTests(BaseBookingTests):
    def setUp(self):
        self.driver_patches = ExitStack()
        self.addCleanup(self.driver_patches.close)

        def driver_connect(path):
            connection = RemoteConnection(libsql.connect(str(path), timeout=0))
            connection.execute("PRAGMA foreign_keys=ON")
            return connection

        for module in ("backend.database", "backend.web", "backend.booking", "backend.messaging", "backend.notifications", "test_app"):
            self.driver_patches.enter_context(patch(module + ".connect", side_effect=driver_connect))
        super().setUp()

    def test_named_and_positional_rows(self):
        with RemoteConnection(libsql.connect(":memory:")) as db:
            row = db.execute("SELECT 7 AS id, 'Layanan' AS name").fetchone()
            self.assertEqual(row[0], 7)
            self.assertEqual(row["name"], "Layanan")
            self.assertEqual(dict(row), {"id": 7, "name": "Layanan"})
            self.assertIsNone(db.execute("SELECT 1 WHERE 0").fetchone())

    def test_turso_token_is_not_in_repr(self):
        self.assertNotIn("private-token", repr(TursoDatabase("libsql://example.turso.io", "private-token")))


# Do not rediscover the imported base class from this module.
del BaseBookingTests
