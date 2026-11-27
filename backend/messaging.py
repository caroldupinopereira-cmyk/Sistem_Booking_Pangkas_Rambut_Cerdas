"""Outbox email: simulasi default; SMTP memerlukan konfigurasi lengkap."""

import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage
from .database import connect
from .booking import now, stamp, get_booking, parse


def _build(b, kind, link):
    when = parse(b["start"]).strftime("%d/%m/%Y %H:%M") + " WIB"
    titles = {
        "confirmation": "Konfirmasi booking",
        "reschedule": "Perubahan jadwal booking",
        "cancellation": "Pembatalan booking",
        "reminder": "Pengingat booking",
    }
    intros = {
        "confirmation": "Booking Anda berhasil dibuat.",
        "reschedule": "Jadwal booking Anda telah diperbarui.",
        "cancellation": "Booking Anda telah dibatalkan.",
        "reminder": "Ini pengingat untuk jadwal booking Anda.",
    }
    subject = f"{titles[kind]} - {b['code']}"
    body = (
        f"Halo {b['name']},\n\n"
        f"{intros[kind]}\n\n"
        f"Layanan : {b['service_name']}\n"
        f"Kapster : {b['barber_name']}\n"
        f"Jadwal  : {when}\n"
        f"Kode    : {b['code']}\n\n"
        f"Kelola booking: {link}\n\n"
        "Terima kasih."
    )
    preview = (
        f"{titles[kind]}. Halo {b['name']}, {b['service_name']} bersama {b['barber_name']} "
        f"pada {when}. Kode {b['code']}. Kelola: {link}"
    )
    return subject, body, preview


def smtp_health(config):
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")
    missing = [key for key in required if not str(config.get(key) or "").strip()]
    invalid = [key for key in required if str(config.get(key) or "").startswith(key + "=")]
    if "SMTP_PORT" not in missing:
        try:
            if not 1 <= int(config["SMTP_PORT"]) <= 65535:
                invalid.append("SMTP_PORT")
        except (ValueError, TypeError):
            invalid.append("SMTP_PORT")
    mode = str(config.get("MAIL_MODE", "demo") or "demo").strip().lower()
    return {"mode": mode, "ready": mode == "smtp" and not missing and not invalid,
            "missing": missing, "invalid": sorted(set(invalid)),
            "checks": [{"name": key, "configured": key not in missing and key not in invalid} for key in required]}


def _smtp_ready(config):
    health = smtp_health(config)
    return not health["missing"] and not health["invalid"]


def smtp_error(error):
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "failed", "Autentikasi SMTP gagal. Periksa email dan App Password."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "failed", "Alamat email penerima ditolak oleh server."
    return "unknown", "Status pengiriman email tidak pasti. Periksa penyedia; tidak dicoba ulang otomatis."


def _send_smtp(config, to_addr, subject, body):
    host = str(config.get("SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com").strip()
    try:
        port = int(config.get("SMTP_PORT", 587))
    except (ValueError, TypeError):
        port = 587
    user = str(config.get("SMTP_USER", "")).strip()
    password = str(config.get("SMTP_PASSWORD", ""))
    if host.strip().lower() == "smtp.gmail.com":
        password = password.replace(" ", "")
    sender = str(config.get("MAIL_FROM", "") or user).strip() or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(body)
    safe = (
        body.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
    msg.add_alternative(f"<html><body><p>{safe}</p></body></html>", subtype="html")

    context = ssl.create_default_context()
    transport = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    options = {"context": context} if port == 465 else {}
    with transport(host, port, timeout=10, **options) as server:
        server.ehlo()
        if port != 465:
            server.starttls(context=context)
            server.ehlo()
        server.login(user, password)
        server.send_message(msg)
    return msg.get("Message-ID")


def send_verification_code(path, config, email, code):
    """Kirim kode verifikasi 6 digit secara sinkron. Jangan log token/secret."""
    subject = "Kode verifikasi akun - Ruang Pangkas"
    body = (
        "Halo,\n\n"
        f"Kode verifikasi akun Anda: {code}\n"
        "Kode berlaku 10 menit dan hanya dapat dipakai sekali.\n\n"
        "Jika Anda tidak meminta kode ini, abaikan email ini."
    )
    preview = f"Kode verifikasi dikirim ke {email}. Berlaku 10 menit."
    mode = str(config.get("MAIL_MODE", "demo") or "demo").strip().lower()
    status = "simulated"
    error = ""
    if mode == "demo":
        logging.warning("[DEMO] Kode verifikasi untuk %s: %s", email, code)
    else:
        if not _smtp_ready(config):
            raise RuntimeError("smtp_unavailable")
        try:
            _send_smtp(config, email, subject, body)
            status = "accepted"
        except Exception:
            raise RuntimeError("smtp_failed")
    with connect(path) as db:
        db.execute(
            "INSERT INTO verification_messages(email,kind,created,status,preview,error) VALUES (?,?,?,?,?,?)",
            (email, "verification", stamp(now()), status, preview, error),
        )
    return True


def process_messages(path, config, limit=10, include_reminders=True, message_id=None, booking_token=None):
    """Claim durably before SMTP; never hold a remote write transaction during I/O.

    A process interrupted after claiming leaves 'dispatching' for manual review.
    Such messages are never automatically retried because SMTP has no idempotency key.
    """
    with connect(path) as db:
        rows = db.execute(
            "SELECT id FROM messages WHERE status='pending' AND due<=? "
            + ("" if include_reminders else "AND kind!='reminder' ")
            + ("AND id=? " if message_id is not None else "")
            + ("AND booking_id=(SELECT id FROM bookings WHERE token=?) " if booking_token else "")
            + "ORDER BY due,id LIMIT ?",
            tuple([stamp(now())] + ([int(message_id)] if message_id is not None else []) + ([booking_token] if booking_token else []) + [int(limit)]),
        ).fetchall()
    count = 0
    for candidate in rows:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            m = db.execute("SELECT * FROM messages WHERE id=? AND status='pending'", (candidate["id"],)).fetchone()
            if not m:
                continue
            raw = db.execute("SELECT token FROM bookings WHERE id=?", (m["booking_id"],)).fetchone()
            b = get_booking(db, raw["token"]) if raw else None
            if not b or b["version"] != m["version"] or (
                m["kind"] == "reminder"
                and (b["status"] != "scheduled" or parse(b["start"]) <= now())
            ):
                db.execute("UPDATE messages SET status='obsolete' WHERE id=?", (m["id"],))
                continue
            link = str(config.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")).rstrip("/") + "/booking/" + b["token"]
            subject, body, preview = _build(b, m["kind"], link)
            db.execute(
                "UPDATE messages SET status='dispatching',preview=?,processed_at=? WHERE id=?",
                (preview, stamp(now()), m["id"]),
            )
        # The committed claim prevents concurrent invocations from sending twice.
        status, error, pid = "simulated", "", None
        mode = str(config.get("MAIL_MODE", "demo") or "demo").strip().lower()
        if mode == "smtp":
            if not _smtp_ready(config):
                status, error = "failed", "Konfigurasi SMTP belum lengkap."
            else:
                try:
                    pid = _send_smtp(config, b["email"], subject, body)
                    status = "accepted"
                except Exception as exc:
                    status, error = smtp_error(exc)
        with connect(path) as db:
            db.execute(
                "UPDATE messages SET status=?,error=?,provider_id=?,processed_at=? WHERE id=? AND status='dispatching'",
                (status, error, pid, stamp(now()), m["id"]),
            )
        count += 1
    return count
