"""Admin inbox plus durable email/Web Push delivery claims."""
import base64
import hashlib
import json
from urllib.parse import urlsplit
from database import connect, settings

TITLES = {"booking_dibuat": "Booking baru", "jadwal_diubah": "Jadwal booking diubah", "dibatalkan": "Booking dibatalkan"}


def record_event(db, history_id, booking_id, action, actor, created):
    if actor != "pelanggan" or action not in TITLES:
        return
    b = db.execute("SELECT b.*,s.name service_name,k.name barber_name FROM bookings b JOIN services s ON s.id=b.service_id JOIN barbers k ON k.id=b.barber_id WHERE b.id=?", (booking_id,)).fetchone()
    if not b or b["source"] != "online":
        return
    body = f"{b['name']} · {b['service_name']} · {b['barber_name']} · {b['start'].replace('T', ' ')} WIB · {b['code']}"
    cursor = db.execute("INSERT INTO admin_notifications(history_id,booking_id,title,body,created) VALUES (?,?,?,?,?)", (history_id, booking_id, TITLES[action], body, created))
    db.execute("INSERT INTO push_deliveries(notification_id,subscription_id) SELECT ?,id FROM push_subscriptions WHERE active=1", (cursor.lastrowid,))


def inbox(db):
    count = db.execute("SELECT COUNT(*) FROM admin_notifications WHERE read_at IS NULL").fetchone()[0]
    rows = db.execute("SELECT n.*,b.token FROM admin_notifications n JOIN bookings b ON b.id=n.booking_id ORDER BY n.id DESC LIMIT 20").fetchall()
    return {"unread": count, "latest_id": rows[0]["id"] if rows else 0, "items": [dict(row) for row in rows]}


def mark_read(db, through_id, at):
    db.execute("UPDATE admin_notifications SET read_at=? WHERE read_at IS NULL AND id<=?", (at, through_id))


def ensure_push_keys(db):
    cfg = settings(db)
    if not cfg.get("_push_private_key"):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        public = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")
        db.executemany("INSERT INTO settings(key,value) VALUES (?,?)", [("_push_private_key", private), ("_push_public_key", public)])
        return public
    return cfg["_push_public_key"]


def validate_subscription(data):
    if not isinstance(data, dict):
        raise ValueError("Data perangkat tidak valid.")
    endpoint = str(data.get("endpoint", ""))
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    approved = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com"} or host.endswith(".notify.windows.com") or host.endswith(".push.apple.com")
    if len(endpoint) > 2048 or parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None,443) or not approved:
        raise ValueError("Alamat layanan push tidak didukung.")
    keys = data.get("keys", {})
    try:
        public = base64.b64decode(keys["p256dh"] + "=" * (-len(keys["p256dh"]) % 4), altchars=b"-_", validate=True)
        auth = base64.b64decode(keys["auth"] + "=" * (-len(keys["auth"]) % 4), altchars=b"-_", validate=True)
        from cryptography.hazmat.primitives.asymmetric import ec
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
        if len(auth) != 16:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Kunci perangkat tidak valid.") from None
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


def save_subscription(db, data, admin_email, created):
    data = validate_subscription(data)
    endpoint = data["endpoint"]
    admin = db.execute("SELECT * FROM admins WHERE email=?", (admin_email,)).fetchone()
    if not admin:
        raise ValueError("Akun admin tidak tersedia.")
    existing = db.execute("SELECT id,active FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
    if (not existing or not existing["active"]) and db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE active=1").fetchone()[0] >= 3:
        raise ValueError("Maksimal tiga perangkat aktif. Nonaktifkan perangkat lain terlebih dahulu.")
    fingerprint = hashlib.sha256(admin["password"].encode()).hexdigest()
    db.execute("INSERT INTO push_subscriptions(endpoint,subscription,admin_id,credential,created,active) VALUES (?,?,?,?,?,1) ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription,admin_id=excluded.admin_id,credential=excluded.credential,active=1", (endpoint, json.dumps(data), admin["id"], fingerprint, created))


def _send_push(subscription, private_key, admin_email, title, notification_id):
    from pywebpush import webpush
    from py_vapid import Vapid
    # Lock-screen payload deliberately omits customer identity and private links.
    return webpush(subscription_info=json.loads(subscription), data=json.dumps({"title": title, "body": "Ada pembaruan booking. Buka dashboard admin untuk melihat detail.", "url": "/admin#notifikasi", "tag": "booking-" + str(notification_id)}), vapid_private_key=Vapid.from_pem(private_key.encode()), vapid_claims={"sub": "mailto:" + admin_email}, timeout=4, ttl=3600)


def process_admin_notifications(path, config, limit=1):
    from messaging import _send_smtp, _smtp_ready
    with connect(path) as db:
        rows = db.execute("SELECT id FROM admin_notifications n WHERE email_status='pending' OR EXISTS (SELECT 1 FROM push_deliveries p WHERE p.notification_id=n.id AND p.status='pending') ORDER BY id LIMIT ?", (limit,)).fetchall()
    count = 0
    for row in rows:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            event = db.execute("SELECT * FROM admin_notifications WHERE id=?", (row["id"],)).fetchone()
            admin = db.execute("SELECT * FROM admins LIMIT 1").fetchone()
            send_email = event["email_status"] == "pending"
            if send_email:
                db.execute("UPDATE admin_notifications SET email_status='dispatching' WHERE id=?", (event["id"],))
            pushes = db.execute("SELECT d.id delivery_id,s.* FROM push_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id WHERE d.notification_id=? AND d.status='pending'", (event["id"],)).fetchall()
            for push in pushes:
                db.execute("UPDATE push_deliveries SET status='dispatching' WHERE id=?", (push["delivery_id"],))
            private_key = settings(db).get("_push_private_key", "") if pushes else ""
        if send_email:
            state, error = "simulated", ""
            if not admin:
                state = "skipped"
            elif str(config.get("MAIL_MODE", "demo")).lower() == "smtp":
                try:
                    if not _smtp_ready(config):
                        raise ValueError()
                    body = event["body"] + "\n\nBuka dashboard: " + str(config.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")).rstrip("/") + "/admin#notifikasi"
                    _send_smtp(config, admin["email"], event["title"] + " - Ruang Pangkas", body)
                    state = "accepted"
                except Exception:
                    state, error = "unknown", "Pengiriman email admin perlu diperiksa; tidak dicoba ulang otomatis."
            with connect(path) as db:
                db.execute("UPDATE admin_notifications SET email_status=?,email_error=? WHERE id=?", (state,error,event["id"]))
        for push in pushes:
            state = "simulated"
            valid = admin and push["active"] and push["admin_id"] == admin["id"] and push["credential"] == hashlib.sha256(admin["password"].encode()).hexdigest()
            if not valid:
                state = "obsolete"
            elif str(config.get("PUSH_MODE", "live")).lower() != "demo":
                try:
                    _send_push(push["subscription"],private_key,admin["email"],event["title"],event["id"])
                    state = "accepted"
                except Exception as exc:
                    status = getattr(getattr(exc,"response",None),"status_code",None)
                    state = "expired" if status in (404,410) else "unknown"
            with connect(path) as db:
                db.execute("UPDATE push_deliveries SET status=? WHERE id=?", (state,push["delivery_id"]))
                if state == "expired":
                    db.execute("UPDATE push_subscriptions SET active=0 WHERE id=?", (push["id"],))
        count += 1
    return count
