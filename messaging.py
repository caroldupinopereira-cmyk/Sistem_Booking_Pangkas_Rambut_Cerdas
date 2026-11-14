"""Outbox email: simulasi default; SMTP memerlukan konfigurasi lengkap."""

import logging
import smtplib
import socket
from email.message import EmailMessage
from database import connect
from booking import now, stamp, get_booking, parse


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


def _smtp_ready(config):
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"]
    return all(str(config.get(k, "")).strip() for k in keys)


def _send_smtp(config, to_addr, subject, body):
    host = str(config.get("SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com").strip()
    try:
        port = int(config.get("SMTP_PORT", 587))
    except (ValueError, TypeError):
        port = 587
    user = str(config.get("SMTP_USER", "")).strip()
    password = str(config.get("SMTP_PASSWORD", ""))
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

    with smtplib.SMTP(host, port, timeout=10) as server:
        server.ehlo()
        server.starttls()
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
    mode = str(config.get("MAIL_MODE", "demo") or "demo").lower()
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


def process_messages(path, config, limit=10, include_reminders=True):
    """Claim durably before SMTP; never hold a remote write transaction during I/O.

    A process interrupted after claiming leaves 'dispatching' for manual review.
    Such messages are never automatically retried because SMTP has no idempotency key.
    """
    with connect(path) as db:
        rows = db.execute(
            "SELECT id FROM messages WHERE status='pending' AND due<=? "
            + ("" if include_reminders else "AND kind!='reminder' ")
            + "ORDER BY due,id LIMIT ?",
            (stamp(now()), int(limit)),
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
        mode = str(config.get("MAIL_MODE", "demo") or "demo").lower()
        if mode == "smtp":
            if not _smtp_ready(config):
                status, error = "failed", "Konfigurasi SMTP belum lengkap."
            else:
                try:
                    pid = _send_smtp(config, b["email"], subject, body)
                    status = "accepted"
                except smtplib.SMTPAuthenticationError:
                    status, error = "failed", "Autentikasi SMTP gagal. Periksa email dan App Password."
                except smtplib.SMTPRecipientsRefused:
                    status, error = "failed", "Alamat email penerima ditolak oleh server."
                except Exception:
                    status, error = "unknown", "Status pengiriman email tidak pasti. Periksa penyedia; tidak dicoba ulang otomatis."
        with connect(path) as db:
            db.execute(
                "UPDATE messages SET status=?,error=?,provider_id=?,processed_at=? WHERE id=? AND status='dispatching'",
                (status, error, pid, stamp(now()), m["id"]),
            )
        count += 1
    return count
