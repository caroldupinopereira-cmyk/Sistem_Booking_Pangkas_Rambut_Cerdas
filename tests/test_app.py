import unittest, tempfile, threading, smtplib, socket
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor
from database import connect, init_db
from booking import (
    create_booking,
    change_booking,
    get_booking,
    slots,
    update_status,
    BookingError,
    normalize_email,
    stamp,
)
from messaging import process_messages
from analytics import insights
from app import create_app

BASE = datetime(2026, 10, 9, 8, 0)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / "work"
        self.test_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.path = Path(self.tmp.name) / "test.sqlite3"
        init_db(self.path)
        self.clock = BASE
        self.patches = [
            patch(m + ".now", side_effect=lambda: self.clock)
            for m in ["booking", "messaging", "analytics", "app"]
        ]
        for p in self.patches:
            p.start()
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.path),
                "SECRET_KEY": "test-secret",
                "MAIL_MODE": "demo",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.assertTrue(
            Path(self.tmp.name).resolve().is_relative_to(self.test_root.resolve())
        )
        self.tmp.cleanup()

    def make(
        self,
        start="2026-10-10T10:00:00",
        barber=1,
        consent="1",
        source="online",
        email="pelanggan@example.com",
    ):
        return create_booking(
            self.path,
            dict(
                name="Pelanggan Uji",
                email=email,
                service_id=1,
                barber_id=barber,
                start=start,
                email_consent=consent,
            ),
            source=source,
        )

    def row(self, token):
        with connect(self.path) as db:
            return dict(get_booking(db, token))

    def post(self, url, data):
        with self.client.session_transaction() as s:
            s["csrf"] = "test-csrf"
        return self.client.post(url, data={**data, "csrf": "test-csrf"})

    def login(self):
        import hashlib
        from werkzeug.security import generate_password_hash
        with connect(self.path) as db:
            admin = db.execute("SELECT * FROM admins").fetchone()
            if not admin:
                db.execute("INSERT INTO admins(email,password) VALUES (?,?)", ("admin@example.com", generate_password_hash("test-password-admin")))
                admin = db.execute("SELECT * FROM admins").fetchone()
        with self.client.session_transaction() as s:
            s["admin"] = admin["email"]
            s["admin_credential"] = hashlib.sha256(admin["password"].encode()).hexdigest()

    def test_duplicate_and_overlapping_slots(self):
        self.make()
        for date in ["2026-10-10T10:00:00", "2026-10-10T10:15:00"]:
            with self.assertRaises(BookingError):
                self.make(date)
        self.make("2026-10-10T10:30:00")
        self.make(barber=2)

    def test_concurrent_booking_only_one_succeeds(self):
        barrier = threading.Barrier(2)

        def run(_):
            barrier.wait()
            try:
                self.make()
                return True
            except BookingError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run, range(2))), 1)

    def test_break_closing_and_past(self):
        for start in [
            "2026-10-10T11:45:00",
            "2026-10-10T19:45:00",
            "2026-10-08T10:00:00",
        ]:
            with self.assertRaises(BookingError):
                self.make(start)

    def test_invalid_time_grid(self):
        with self.assertRaises(BookingError):
            self.make("2026-10-10T10:07:00")

    def test_blocks_and_off_day(self):
        with connect(self.path) as db:
            db.execute(
                "INSERT INTO blocks(barber_id,start,end,reason) VALUES (1,?,?,?)",
                ("2026-10-10T09:00:00", "2026-10-10T11:00:00", "Libur"),
            )
        with self.assertRaises(BookingError):
            self.make()
        self.make(barber=2)

    def test_failed_reschedule_keeps_original(self):
        a = self.make()
        self.make("2026-10-10T11:00:00")
        with self.assertRaises(BookingError):
            change_booking(self.path, a, "reschedule", "2026-10-10T11:00:00")
        self.assertEqual(self.row(a)["start"], "2026-10-10T10:00:00")
        self.assertEqual(self.row(a)["version"], 1)

    def test_reschedule_and_old_reminder_invalidated(self):
        a = self.make()
        change_booking(self.path, a, "reschedule", "2026-10-10T14:00:00")
        self.make("2026-10-10T10:00:00")
        with connect(self.path) as db:
            old = db.execute(
                "SELECT status FROM messages WHERE booking_id=? AND version=1",
                (self.row(a)["id"],),
            ).fetchall()
        self.assertTrue(all(r["status"] == "obsolete" for r in old))
        self.assertEqual(self.row(a)["version"], 2)

    def test_one_hour_boundary(self):
        a = self.make("2026-10-09T10:00:00")
        self.clock = datetime(2026, 10, 9, 9, 0)
        change_booking(self.path, a, "reschedule", "2026-10-09T11:00:00")
        self.clock = datetime(2026, 10, 9, 10, 0, 1)
        with self.assertRaises(BookingError):
            change_booking(self.path, a, "cancel")

    def test_admin_override_boundary(self):
        a = self.make("2026-10-09T10:00:00")
        self.clock = datetime(2026, 10, 9, 9, 30)
        change_booking(self.path, a, "cancel", actor="admin")
        self.assertEqual(self.row(a)["status"], "cancelled")

    def test_cancel_releases_slot_and_preserves_history(self):
        a = self.make()
        change_booking(self.path, a, "cancel")
        self.make()
        with connect(self.path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM history WHERE booking_id=?",
                    (self.row(a)["id"],),
                ).fetchone()[0],
                2,
            )

    def test_no_reminder_under_two_hours(self):
        a = self.make("2026-10-09T09:00:00")
        with connect(self.path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM messages WHERE booking_id=?",
                    (self.row(a)["id"],),
                ).fetchone()[0],
                1,
            )

    def test_demo_messages_idempotent(self):
        self.make()
        self.assertEqual(process_messages(self.path, {"MAIL_MODE": "demo"}), 1)
        self.assertEqual(process_messages(self.path, {"MAIL_MODE": "demo"}), 0)
        with connect(self.path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM messages WHERE status='simulated'"
                ).fetchone()[0],
                1,
            )

    def test_cancel_stops_reminder(self):
        a = self.make()
        change_booking(self.path, a, "cancel")
        self.clock = datetime(2026, 10, 10, 8, 0)
        process_messages(self.path, {"MAIL_MODE": "demo"})
        with connect(self.path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT status FROM messages WHERE kind='reminder'"
                ).fetchone()[0],
                "obsolete",
            )

    def test_consent_and_walkin(self):
        self.make(consent="")
        create_booking(
            self.path,
            dict(
                name="Walk In",
                email="",
                service_id=1,
                barber_id=2,
                start="2026-10-10T10:00:00",
            ),
            source="walkin",
        )
        with connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM messages").fetchone()[0], 0
            )

    def test_status_sequence_and_payment_rollback(self):
        a = self.make("2026-10-09T10:00:00")
        self.clock = datetime(2026, 10, 9, 10, 0)
        with self.assertRaises(BookingError):
            update_status(self.path, a, "completed", 35000)
        update_status(self.path, a, "arrived")
        update_status(self.path, a, "serving")
        with self.assertRaises(BookingError):
            update_status(self.path, a, "completed", "bad")
        self.assertEqual(self.row(a)["status"], "serving")
        update_status(self.path, a, "completed", "35000")
        self.assertEqual(self.row(a)["paid"], 35000)

    def test_no_show_needs_tolerance_and_time(self):
        a = self.make("2026-10-09T10:00:00")
        self.clock = datetime(2026, 10, 9, 10, 1)
        with self.assertRaises(BookingError):
            update_status(self.path, a, "no_show")
        with connect(self.path) as db:
            db.execute("UPDATE settings SET value='15' WHERE key='tolerance'")
        with self.assertRaises(BookingError):
            update_status(self.path, a, "no_show")
        self.clock = datetime(2026, 10, 9, 10, 16)
        update_status(self.path, a, "no_show")

    def test_empty_analytics_is_not_fake(self):
        with connect(self.path) as db:
            d = insights(db)
        self.assertFalse(d["ready"])
        self.assertFalse(d["risk_ready"])
        self.assertEqual(d["total_attended"], 0)
        self.assertTrue(all(f["value"] is None for f in d["forecast"]))

    def test_csrf_and_admin_access(self):
        self.assertEqual(self.client.post("/book", data={}).status_code, 400)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_all_pages_render(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/setup").status_code, 200)
        a = self.make()
        self.assertEqual(self.client.get("/booking/" + a).status_code, 200)
        self.login()
        for url in [
            "/admin",
            "/admin/settings",
            "/admin/walkin",
            "/admin/messages",
            "/admin/analytics",
            "/booking/" + a,
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_setup_password_and_login(self):
        self.post(
            "/setup",
            dict(email="PEMILIK@example.com", password="contoh-password-ku"),
        )
        self.post("/logout", {})
        self.assertEqual(
            self.post(
                "/login", dict(email="pemilik@example.com", password="salah")
            ).status_code,
            200,
        )
        self.assertEqual(
            self.post(
                "/login",
                dict(email="pemilik@example.com", password="contoh-password-ku"),
            ).status_code,
            302,
        )

    def test_api_slots_and_unknown_booking(self):
        data = self.client.get(
            "/api/slots?service=1&barber=1&date=2026-10-10"
        ).get_json()
        self.assertTrue(data["slots"])
        self.assertEqual(
            self.client.get("/api/slots?service=1&barber=1&date=bad").status_code, 400
        )
        self.assertEqual(self.client.get("/booking/not-a-token").status_code, 404)

    def test_customer_name_is_escaped(self):
        a = create_booking(
            self.path,
            dict(
                name="<script>alert(1)</script>",
                email="aman@example.com",
                service_id=1,
                barber_id=1,
                start="2026-10-10T10:00:00",
            ),
        )
        body = self.client.get("/booking/" + a).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_email_validation_and_normalization(self):
        self.assertEqual(normalize_email("  Nama@Gmail.COM  "), "nama@gmail.com")
        for bad in ["", "nama", "nama@", "@gmail.com", "nama gmail.com", "a@b", " "]:
            with self.assertRaises(BookingError):
                normalize_email(bad)
        self.assertEqual(normalize_email("", required=False), "")
        self.assertEqual(normalize_email("A@B.C").count("@"), 1)

    def test_online_without_email_rejected_walkin_without_email_accepted(self):
        with self.assertRaises(BookingError):
            create_booking(
                self.path,
                dict(
                    name="Pelanggan Uji",
                    email="",
                    service_id=1,
                    barber_id=1,
                    start="2026-10-10T10:00:00",
                    email_consent="1",
                ),
                source="online",
            )
        token = create_booking(
            self.path,
            dict(
                name="Walk In",
                email="",
                service_id=1,
                barber_id=1,
                start="2026-10-10T10:00:00",
            ),
            source="walkin",
        )
        self.assertEqual(self.row(token)["email"], "")

    def test_no_message_when_email_consent_zero(self):
        self.make(consent="0")
        with connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM messages").fetchone()[0], 0
            )

    def test_demo_mode_does_not_open_smtp(self):
        self.make()
        with patch("messaging.smtplib.SMTP") as smtp:
            n = process_messages(self.path, {"MAIL_MODE": "demo"})
            self.assertEqual(n, 1)
            smtp.assert_not_called()

    def _smtp_config(self):
        return {
            "MAIL_MODE": "smtp",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "pengirim@example.com",
            "SMTP_PASSWORD": "app-password-rahasia",
            "MAIL_FROM": "pengirim@example.com",
            "PUBLIC_BASE_URL": "http://127.0.0.1:5000",
        }

    def test_smtp_mode_uses_smtplib_once(self):
        self.make()
        with patch("messaging.smtplib.SMTP") as smtp:
            server = MagicMock()
            smtp.return_value.__enter__.return_value = server
            n = process_messages(self.path, self._smtp_config())
            self.assertEqual(n, 1)
            smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
            server.starttls.assert_called_once()
            server.login.assert_called_once_with(
                "pengirim@example.com", "app-password-rahasia"
            )
            server.send_message.assert_called_once()
        with connect(self.path) as db:
            row = db.execute("SELECT status,error FROM messages").fetchone()
            self.assertEqual(row["status"], "accepted")
            self.assertEqual(row["error"], "")

    def test_smtp_error_no_retry_and_no_password_leak(self):
        cases = [
            (smtplib.SMTPAuthenticationError(535, b"auth"), "failed"),
            (smtplib.SMTPRecipientsRefused({"x": (550, b"bad")}), "failed"),
            (socket.timeout("timeout"), "unknown"),
        ]
        for exc, expected in cases:
            with self.subTest(error=type(exc).__name__):
                self.tearDown()
                self.setUp()
                self.make()
                with patch("messaging.smtplib.SMTP") as smtp:
                    server = MagicMock()
                    smtp.return_value.__enter__.return_value = server
                    server.send_message.side_effect = exc
                    n1 = process_messages(self.path, self._smtp_config())
                    self.assertEqual(n1, 1)
                    self.assertEqual(server.send_message.call_count, 1)
                    n2 = process_messages(self.path, self._smtp_config())
                    self.assertEqual(n2, 0)
                    self.assertEqual(server.send_message.call_count, 1)
                with connect(self.path) as db:
                    row = db.execute("SELECT status,error FROM messages").fetchone()
                    self.assertEqual(row["status"], expected)
                    self.assertNotIn("app-password-rahasia", row["error"] or "")


    def test_hosting_environment_and_https_cookie(self):
        with patch.dict("os.environ", {
            "SECRET_KEY": "hosting-test-secret",
            "DATABASE": str(self.path),
            "PUBLIC_BASE_URL": "https://booking.example.com",
            "VERCEL": "0",
            "TURSO_DATABASE_URL": "",
        }):
            hosted = create_app({"TESTING": True, "MAIL_MODE": "demo"})
        self.assertEqual(hosted.config["SECRET_KEY"], "hosting-test-secret")
        self.assertEqual(hosted.config["DATABASE"], str(self.path))
        self.assertTrue(hosted.config["SESSION_COOKIE_SECURE"])
        self.assertFalse(self.app.config["SESSION_COOKIE_SECURE"])

    def test_vercel_rejects_local_database_before_initialization(self):
        with patch.dict("os.environ", {"VERCEL": "1", "SECRET_KEY": "hosting-test-secret"}):
            with patch("app.init_db") as initialize:
                with self.assertRaisesRegex(RuntimeError, "SQLite lokal"):
                    create_app({"TESTING": True, "DATABASE": str(self.path)})
                initialize.assert_not_called()


    def test_admin_singleton_and_setup_closed(self):
        import sqlite3
        self.login()
        with self.assertRaises(sqlite3.IntegrityError):
            with connect(self.path) as db:
                db.execute("INSERT INTO admins(email,password) VALUES (?,?)", ("second@example.com", "hash"))
        self.assertEqual(self.post("/setup", {"email": "second@example.com", "password": "another-password"}).status_code, 302)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admins").fetchone()[0], 1)

    def test_admin_replacement_requires_code_and_invalidates_sessions(self):
        from admin_account import replace_admin
        from werkzeug.security import check_password_hash
        self.login()
        code = "test-special-code-" + "x" * 32
        with self.assertRaisesRegex(ValueError, "tidak cocok"):
            replace_admin(self.path, code, "wrong", "new@example.com", "new-password-admin")
        self.assertEqual(self.client.get("/admin").status_code, 200)
        replace_admin(self.path, code, code, "admin@example.com", "new-password-admin")
        self.assertEqual(self.client.get("/admin").status_code, 302)
        with connect(self.path) as db:
            account = db.execute("SELECT * FROM admins").fetchone()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admins").fetchone()[0], 1)
            self.assertTrue(check_password_hash(account["password"], "new-password-admin"))
        self.assertEqual(self.post("/login", {"email": "admin@example.com", "password": "new-password-admin"}).status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 200)
        replace_admin(self.path, code, code, "new@example.com", "final-password-admin")
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.assertEqual(self.post("/login", {"email": "new@example.com", "password": "final-password-admin"}).status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 200)


    def test_email_claim_does_not_hold_database_lock_during_smtp(self):
        self.make()
        def send_without_lock(*args):
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertEqual(db.execute("SELECT status FROM messages WHERE kind='confirmation'").fetchone()[0], "dispatching")
        with patch("messaging._send_smtp", side_effect=send_without_lock) as sender:
            self.assertEqual(process_messages(self.path, self._smtp_config()), 1)
            self.assertEqual(sender.call_count, 1)

    def test_interrupted_email_claim_is_not_retried(self):
        self.make()
        with patch("messaging._send_smtp", side_effect=SystemExit("simulated interruption")):
            with self.assertRaises(SystemExit):
                process_messages(self.path, self._smtp_config())
        with patch("messaging._send_smtp", return_value=None) as sender:
            self.assertEqual(process_messages(self.path, self._smtp_config()), 0)
            sender.assert_not_called()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM messages WHERE kind='confirmation'").fetchone()[0], "dispatching")

    def test_cron_requires_secret_and_limits_processing(self):
        self.app.config["CRON_SECRET"] = "x" * 40
        with patch("app.process_messages", return_value=1) as processor:
            self.assertEqual(self.client.get("/api/cron/messages").status_code, 401)
            self.assertEqual(self.client.get("/api/cron/messages", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            processor.assert_not_called()
            response = self.client.get("/api/cron/messages", headers={"Authorization": "Bearer " + "x" * 40})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"processed": 1})
            processor.assert_called_once_with(self.app.config["DATABASE"], self.app.config, limit=1)

    def test_vercel_change_dispatches_transaction_email(self):
        token = self.make()
        self.login()
        with patch.dict("os.environ", {"VERCEL": "1"}):
            with patch("app.process_messages", return_value=1) as processor:
                response = self.post("/booking/" + token + "/change", {"action": "cancel"})
                self.assertEqual(response.status_code, 302)
                processor.assert_called_once_with(self.app.config["DATABASE"], self.app.config, limit=1, include_reminders=False)


    def test_concurrent_email_dispatch_sends_only_once(self):
        self.make()
        barrier = threading.Barrier(2)
        def run(_):
            barrier.wait()
            return process_messages(self.path, self._smtp_config())
        with patch("messaging._send_smtp", return_value=None) as sender:
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(run, range(2))), 1)
            self.assertEqual(sender.call_count, 1)


    def test_slot_listing_uses_five_queries(self):
        from types import SimpleNamespace
        with connect(self.path) as db:
            execute = MagicMock(wraps=db.execute)
            result = slots(SimpleNamespace(execute=execute), 1, 1, "2026-10-10", at=BASE)
            self.assertGreater(len(result), 20)
            self.assertEqual(execute.call_count, 5)

    def test_bulk_slots_match_authoritative_validation(self):
        from booking import validate_slot, parse
        from datetime import timedelta
        token = self.make()
        with connect(self.path) as db:
            db.execute("INSERT INTO blocks(barber_id,start,end,reason) VALUES (NULL,?,?,?)", ("2026-10-10T14:10:00", "2026-10-10T14:40:00", "Blokir bersama"))
            db.execute("UPDATE hours SET opened='09:07' WHERE weekday=5")
        with connect(self.path) as db:
            booking_id = get_booking(db, token)["id"]
            for barber, exclude, duration in [(1,None,45),(2,None,45),(1,booking_id,30)]:
                with self.subTest(barber=barber, exclude=exclude):
                    expected = []
                    candidate = parse("2026-10-10T09:00:00")
                    while candidate < parse("2026-10-10T20:00:00"):
                        try:
                            validate_slot(db,barber,candidate,duration,exclude=exclude,at=BASE)
                            expected.append(stamp(candidate))
                        except BookingError:
                            pass
                        candidate += timedelta(minutes=15)
                    actual = slots(db,1,barber,"2026-10-10",exclude=exclude,at=BASE,duration=duration)
                    self.assertEqual([slot["value"] for slot in actual],expected)


    def test_vercel_boot_does_not_reinitialize_remote_schema(self):
        from database import TursoDatabase
        with patch.dict("os.environ", {"VERCEL": "1", "SECRET_KEY": "test-hosting-secret"}):
            with patch("app.init_db") as initialize:
                with patch("app.connect", side_effect=lambda target: connect(self.path)):
                    hosted = create_app({"TESTING": True, "DATABASE": TursoDatabase("libsql://example.turso.io", "test-token")})
        initialize.assert_not_called()
        self.assertTrue(hosted.config["TESTING"])


if __name__ == "__main__":
    unittest.main()
