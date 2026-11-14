from pathlib import Path
from functools import wraps
from datetime import timedelta
import os, secrets, threading, time, logging, hmac, hashlib, json
from urllib import request as urlreq, parse as urlparse, error as urlerror
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from database import connect, init_db, settings, TursoDatabase
from booking import (
    BookingError,
    now,
    stamp,
    parse,
    slots,
    create_booking,
    get_booking,
    get_customer,
    customer_bookings,
    change_booking,
    update_status,
    normalize_email,
)
from messaging import process_messages, send_verification_code
from analytics import insights

ROOT = Path(__file__).resolve().parent
STATUS = {
    "scheduled": "Terjadwal",
    "arrived": "Sudah datang",
    "serving": "Dilayani",
    "completed": "Selesai",
    "cancelled": "Dibatalkan",
    "no_show": "No-show",
}
DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def _http_json(url, method="GET", data=None, headers=None, timeout=10):
    body = None
    if data is not None:
        body = urlparse.urlencode(data).encode()
        headers = {**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"}
    req = urlreq.Request(url, data=body, headers=headers or {}, method=method)
    with urlreq.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_app(test_config=None):
    app = Flask(__name__)
    env = {}
    if (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    # Hosting supplies a stable secret; local development keeps its existing key.
    configured_secret = os.environ.get("SECRET_KEY") or env.get("SECRET_KEY")
    if test_config and test_config.get("SECRET_KEY"):
        configured_secret = test_config["SECRET_KEY"]
    instance = ROOT / "instance"
    if not configured_secret:
        if os.environ.get("VERCEL") == "1":
            raise RuntimeError("Isi SECRET_KEY di Environment Variables Vercel.")
        instance.mkdir(exist_ok=True)
        secretfile = instance / "secret.key"
        if not secretfile.exists():
            secretfile.write_text(secrets.token_hex(32), encoding="utf-8")
        configured_secret = secretfile.read_text().strip()
    app.config.update(
        SECRET_KEY=configured_secret,
        DATABASE=str(instance / "barbershop.sqlite3"),
        MAIL_MODE="demo",
        SMTP_HOST="smtp.gmail.com",
        SMTP_PORT="587",
        SMTP_USER="",
        SMTP_PASSWORD="",
        MAIL_FROM="",
        PUBLIC_BASE_URL="http://127.0.0.1:5000",
        GOOGLE_CLIENT_ID="",
        GOOGLE_CLIENT_SECRET="",
        GOOGLE_REDIRECT_URI="http://127.0.0.1:5000/auth/google/callback",
        MAX_CONTENT_LENGTH=65536,
        TEMPLATES_AUTO_RELOAD=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    app.config.update(env)
    for key in [
        "SECRET_KEY",
        "ADMIN_CHANGE_CODE",
        "CRON_SECRET",
        "DATABASE",
        "TURSO_DATABASE_URL",
        "TURSO_AUTH_TOKEN",
        "MAIL_MODE",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "MAIL_FROM",
        "PUBLIC_BASE_URL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
    ]:
        if key in os.environ:
            app.config[key] = os.environ[key]
    if test_config:
        app.config.update(test_config)
    # Derive the stable HTTPS domain on Vercel unless explicitly configured.
    production_domain = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    if os.environ.get("VERCEL") == "1" and production_domain:
        if not os.environ.get("PUBLIC_BASE_URL") and not env.get("PUBLIC_BASE_URL"):
            app.config["PUBLIC_BASE_URL"] = "https://" + production_domain
        if not os.environ.get("GOOGLE_REDIRECT_URI") and not env.get("GOOGLE_REDIRECT_URI"):
            app.config["GOOGLE_REDIRECT_URI"] = app.config["PUBLIC_BASE_URL"].rstrip("/") + "/auth/google/callback"
    app.config["SESSION_COOKIE_SECURE"] = str(app.config["PUBLIC_BASE_URL"]).startswith("https://")
    if not (test_config and "DATABASE" in test_config):
        turso_url = str(app.config.get("TURSO_DATABASE_URL", "")).strip()
        if turso_url:
            app.config["DATABASE"] = TursoDatabase(turso_url, str(app.config.get("TURSO_AUTH_TOKEN", "")).strip())
    if os.environ.get("VERCEL") == "1" and not isinstance(app.config["DATABASE"], TursoDatabase):
        raise RuntimeError("SQLite lokal tidak bisa dipakai di Vercel. Isi TURSO_DATABASE_URL dan TURSO_AUTH_TOKEN.")
    if os.environ.get("VERCEL") == "1":
        # Provision/migrate through the local admin tools before deployment.
        # Replaying schema DDL and seed writes on every cold start costs dozens
        # of network round trips and contends with live booking transactions.
        with connect(app.config["DATABASE"]) as db:
            db.execute("SELECT value FROM settings WHERE key='tracking_started'").fetchone()
    else:
        init_db(app.config["DATABASE"])
        with connect(app.config["DATABASE"]) as db:
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES (?,?)",
                ("tracking_started", stamp(now())),
            )

    def db():
        return connect(app.config["DATABASE"])

    def admin_only(f):
        @wraps(f)
        def inner(*a, **kw):
            if not session.get("admin"):
                return redirect(url_for("login"))
            return f(*a, **kw)

        return inner

    def customer_only(f):
        @wraps(f)
        def inner(*a, **kw):
            if not session.get("customer"):
                return redirect(url_for("masuk"))
            return f(*a, **kw)

        return inner

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def google_enabled():
        return bool(
            str(app.config.get("GOOGLE_CLIENT_ID", "")).strip()
            and str(app.config.get("GOOGLE_CLIENT_SECRET", "")).strip()
        )

    def current_customer(conn=None):
        cid = session.get("customer")
        if not cid:
            return None
        if conn is not None:
            return get_customer(conn, cid)
        with db() as c:
            return get_customer(c, cid)

    def login_customer(customer_id):
        session.clear()
        session["customer"] = int(customer_id)
        session["csrf"] = secrets.token_urlsafe(32)

    def code_hash(email, code):
        secret = str(app.config.get("SECRET_KEY", "")).encode()
        msg = f"{normalize_email(email)}:{code}".encode()
        return hmac.new(secret, msg, hashlib.sha256).hexdigest()

    def issue_verification(email):
        email = normalize_email(email)
        at = now()
        hour_ago = stamp(at - timedelta(hours=1))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            recent = c.execute(
                "SELECT count(*) FROM verification_codes WHERE email=? AND created>=?",
                (email, hour_ago),
            ).fetchone()[0]
            last = c.execute(
                "SELECT created FROM verification_codes WHERE email=? ORDER BY id DESC LIMIT 1",
                (email,),
            ).fetchone()
            if recent >= 5:
                raise BookingError("Terlalu banyak permintaan kode. Coba lagi dalam 1 jam.")
            if last and parse(last["created"]) > at - timedelta(seconds=60):
                raise BookingError("Tunggu 60 detik sebelum meminta kode verifikasi baru.")
            code = f"{secrets.randbelow(1000000):06d}"
            c.execute(
                "INSERT INTO verification_codes(email,code_hash,expires,used,attempts,created) VALUES (?,?,?,?,?,?)",
                (email, code_hash(email, code), stamp(at + timedelta(minutes=10)), 0, 0, stamp(at)),
            )
        try:
            send_verification_code(app.config["DATABASE"], app.config, email, code)
        except Exception:
            with db() as c:
                c.execute(
                    "UPDATE verification_codes SET used=1 WHERE email=? AND used=0 AND code_hash=?",
                    (email, code_hash(email, code)),
                )
            raise BookingError("Kode verifikasi belum dapat dikirim. Coba lagi nanti.")

    @app.before_request
    def protect():
        if session.get("admin"):
            with db() as c:
                account = c.execute("SELECT password FROM admins WHERE email=?", (session["admin"],)).fetchone()
            fingerprint = hashlib.sha256(account["password"].encode()).hexdigest() if account else ""
            if not fingerprint or not secrets.compare_digest(str(session.get("admin_credential", "")), fingerprint):
                session.pop("admin", None)
                session.pop("admin_credential", None)
        if request.method == "POST" and not secrets.compare_digest(
            str(request.form.get("csrf", "")), str(session.get("csrf", "missing"))
        ):
            abort(400, description="Form kedaluwarsa. Muat ulang halaman dan coba lagi.")

    @app.after_request
    def dispatch_serverless_email(response):
        if os.environ.get("VERCEL") == "1" and request.method == "POST" and response.status_code < 400:
            if request.endpoint in {"dasbor_booking", "change", "walkin", "admin_booking"}:
                try:
                    process_messages(app.config["DATABASE"], app.config, limit=1, include_reminders=False)
                except Exception:
                    app.logger.error("Outbox tertunda; periksa Pesan Email atau scheduler.")
        return response

    @app.route("/api/cron/messages")
    def cron_messages():
        secret = str(app.config.get("CRON_SECRET", ""))
        if len(secret) < 32 or not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + secret):
            abort(401)
        count = process_messages(app.config["DATABASE"], app.config, limit=1)
        response = jsonify(processed=count)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith(
            ("/admin", "/booking", "/login", "/setup", "/masuk", "/daftar", "/verifikasi", "/dasbor", "/keluar", "/auth", "/api/")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.context_processor
    def common():
        with db() as c:
            cfg = settings(c)
            has_admin = bool(c.execute("SELECT 1 FROM admins").fetchone())
            cust = None
            cid = session.get("customer")
            if cid:
                cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        return dict(
            asset_version=(ROOT / "static" / "style.css").stat().st_mtime_ns,
            script_version=(ROOT / "static" / "app.js").stat().st_mtime_ns,
            shop=cfg,
            status_labels=STATUS,
            days=DAYS,
            csrf=csrf,
            is_admin=bool(session.get("admin")),
            is_customer=bool(session.get("customer")),
            customer=cust,
            has_admin=has_admin,
            google_enabled=google_enabled(),
            mail_mode=str(app.config.get("MAIL_MODE", "demo")).lower(),
            database_online=isinstance(app.config["DATABASE"], TursoDatabase),
            today=now().date().isoformat(),
        )

    @app.template_filter("rupiah")
    def rupiah(value):
        return "Rp " + f"{int(value or 0):,}".replace(",", ".")

    @app.template_filter("when")
    def when(value):
        if not value:
            return "-"
        dt = parse(value)
        return f"{DAYS[dt.weekday()]}, {dt:%d/%m/%Y · %H:%M}"

    @app.route("/")
    def home():
        with db() as c:
            services = c.execute("SELECT * FROM services WHERE active=1").fetchall()
            barbers = c.execute("SELECT * FROM barbers WHERE active=1").fetchall()
            promos = c.execute(
                "SELECT * FROM promos WHERE active=1 AND date>=? ORDER BY date LIMIT 3",
                (now().date().isoformat(),),
            ).fetchall()
        return render_template("home.html", services=services, barbers=barbers, promos=promos)

    @app.route("/book", methods=["GET", "POST"])
    def book():
        return redirect(url_for("dasbor") if session.get("customer") else url_for("masuk"))

    @app.route("/api/slots")
    def available():
        try:
            with db() as c:
                token = request.args.get("token")
                b = get_booking(c, token) if token else None
                if token and (not b or b["status"] != "scheduled"):
                    raise BookingError("Booking tidak tersedia untuk diubah.")
                if b and parse(b["start"]) - now() < timedelta(hours=1):
                    cust = session.get("customer")
                    owner = cust and b["customer_id"] == cust
                    if not session.get("admin") and not owner:
                        raise BookingError("Batas perubahan sudah lewat. Hubungi admin.")
                service = int(b["service_id"] if b else request.args.get("service", 0))
                barber = int(b["barber_id"] if b else request.args.get("barber", 0))
                result = slots(
                    c, service, barber, request.args.get("date"),
                    exclude=b["id"] if b else None, duration=b["duration"] if b else None,
                )
            return jsonify(slots=result)
        except (BookingError, ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400

    @app.route("/booking/<token>")
    def booking_detail(token):
        with db() as c:
            b = get_booking(c, token)
            if not b:
                abort(404)
            history = c.execute(
                "SELECT * FROM history WHERE booking_id=? ORDER BY id DESC", (b["id"],)
            ).fetchall()
        editable = b["status"] == "scheduled" and parse(b["start"]) - now() >= timedelta(hours=1)
        owner = bool(session.get("customer") and b["customer_id"] == session.get("customer"))
        return render_template("booking.html", b=b, history=history, editable=editable, owner=owner)

    @app.route("/booking/<token>/change", methods=["POST"])
    def change(token):
        try:
            with db() as c:
                b = get_booking(c, token)
                if not b:
                    abort(404)
            if session.get("admin"):
                actor = "admin"
            else:
                cust = session.get("customer")
                if not cust or b["customer_id"] != cust:
                    abort(404)
                actor = "pelanggan"
            change_booking(
                app.config["DATABASE"], token, request.form.get("action"),
                request.form.get("start"), actor=actor,
            )
        except BookingError as e:
            flash(str(e), "error")
            return redirect(url_for("booking_detail", token=token))
        flash("Perubahan booking berhasil disimpan.", "success")
        return redirect(url_for("booking_detail", token=token))

    # --- Akun pelanggan ---
    @app.route("/masuk", methods=["GET", "POST"])
    def masuk():
        if session.get("customer"):
            return redirect(url_for("dasbor"))
        if request.method == "POST":
            attempts = [t for t in session.get("login_attempts", []) if time.time() - t < 300]
            if len(attempts) >= 8:
                flash("Terlalu banyak percobaan. Tunggu 5 menit.", "error")
                return render_template("masuk.html"), 429
            try:
                email = normalize_email(request.form.get("email", ""))
            except BookingError:
                email = ""
            password = request.form.get("password", "")
            email_attempts = session.get("login_email_attempts", {})
            bucket = [t for t in email_attempts.get(email, []) if time.time() - t < 300] if email else []
            if email and len(bucket) >= 8:
                flash("Terlalu banyak percobaan. Tunggu 5 menit.", "error")
                return render_template("masuk.html"), 429
            with db() as c:
                u = c.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone() if email else None
            ok = bool(u and u["password_hash"] and check_password_hash(u["password_hash"], password))
            if ok:
                login_customer(u["id"])
                return redirect(url_for("dasbor"))
            attempts.append(time.time())
            session["login_attempts"] = attempts
            if email:
                bucket.append(time.time())
                email_attempts[email] = bucket
                session["login_email_attempts"] = email_attempts
            flash("Email atau password tidak cocok.", "error")
        return render_template("masuk.html")

    @app.route("/daftar", methods=["GET", "POST"])
    def daftar():
        if session.get("customer"):
            return redirect(url_for("dasbor"))
        if request.method == "POST":
            name = str(request.form.get("name", "")).strip()
            try:
                email = normalize_email(request.form.get("email", ""))
            except BookingError as e:
                flash(str(e), "error")
                return render_template("daftar.html", name=name)
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            consent = 1 if request.form.get("email_consent") == "1" else 0
            if not 2 <= len(name) <= 80:
                flash("Nama harus terdiri dari 2 sampai 80 karakter.", "error")
                return render_template("daftar.html", name=name, email=email)
            if len(password) < 10:
                flash("Password minimal 10 karakter.", "error")
                return render_template("daftar.html", name=name, email=email)
            if password != confirm:
                flash("Ulangi password dengan benar.", "error")
                return render_template("daftar.html", name=name, email=email)
            if not consent:
                flash("Centang persetujuan notifikasi email untuk melanjutkan.", "error")
                return render_template("daftar.html", name=name, email=email)
            with db() as c:
                existing = c.execute("SELECT id FROM customers WHERE email=?", (email,)).fetchone()
                if not existing:
                    c.execute(
                        "INSERT INTO customers(email,name,password_hash,google_sub,email_verified,email_consent,created) VALUES (?,?,?,?,?,?,?)",
                        (email, name, generate_password_hash(password), None, 0, consent, stamp(now())),
                    )
            try:
                issue_verification(email)
            except BookingError as e:
                if "Terlalu banyak" in str(e) or "Tunggu" in str(e):
                    flash(str(e), "error")
                    return render_template("daftar.html", name=name, email=email)
            session["verify_email"] = email
            flash("Jika email ini belum terdaftar, kode verifikasi sudah dikirim.", "success")
            return redirect(url_for("verifikasi"))
        return render_template("daftar.html")

    @app.route("/verifikasi", methods=["GET", "POST"])
    def verifikasi():
        email = session.get("verify_email")
        if not email and session.get("customer"):
            cust = current_customer()
            if cust and not cust["email_verified"]:
                email = cust["email"]
                session["verify_email"] = email
        if request.method == "POST":
            action = request.form.get("action", "verify")
            try:
                email = normalize_email(request.form.get("email") or email or "")
            except BookingError:
                email = ""
            session["verify_email"] = email
            if action == "resend":
                try:
                    if email:
                        issue_verification(email)
                    flash("Jika email ini terdaftar, kode verifikasi sudah dikirim.", "success")
                except BookingError as e:
                    flash(str(e), "error")
                return redirect(url_for("verifikasi"))
            code = str(request.form.get("code", "")).strip()
            if not email or not code.isdigit() or len(code) != 6:
                flash("Kode verifikasi tidak valid.", "error")
                return render_template("verifikasi.html", email=email)
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT * FROM verification_codes WHERE email=? AND used=0 ORDER BY id DESC LIMIT 1",
                    (email,),
                ).fetchone()
                if not row:
                    flash("Kode verifikasi tidak valid atau sudah kedaluwarsa.", "error")
                    return render_template("verifikasi.html", email=email)
                if row["attempts"] >= 5:
                    c.execute("UPDATE verification_codes SET used=1 WHERE id=?", (row["id"],))
                    flash("Kode verifikasi hangus karena terlalu banyak percobaan. Minta kode baru.", "error")
                    return render_template("verifikasi.html", email=email)
                if parse(row["expires"]) < now():
                    c.execute("UPDATE verification_codes SET used=1 WHERE id=?", (row["id"],))
                    flash("Kode verifikasi sudah kedaluwarsa. Minta kode baru.", "error")
                    return render_template("verifikasi.html", email=email)
                if not hmac.compare_digest(row["code_hash"], code_hash(email, code)):
                    c.execute("UPDATE verification_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
                    flash("Kode verifikasi tidak valid.", "error")
                    return render_template("verifikasi.html", email=email)
                c.execute("UPDATE verification_codes SET used=1 WHERE id=?", (row["id"],))
                c.execute("UPDATE customers SET email_verified=1 WHERE email=?", (email,))
                cust = c.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
            if cust:
                session.pop("verify_email", None)
                login_customer(cust["id"])
                flash("Email terverifikasi. Selamat datang di dasbor.", "success")
                return redirect(url_for("dasbor"))
            flash("Kode verifikasi tidak valid.", "error")
        return render_template("verifikasi.html", email=email or "")

    @app.route("/auth/google")
    def auth_google():
        if not google_enabled():
            abort(404)
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        params = {
            "client_id": app.config["GOOGLE_CLIENT_ID"],
            "redirect_uri": app.config["GOOGLE_REDIRECT_URI"],
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlparse.urlencode(params))

    @app.route("/auth/google/callback")
    def auth_google_callback():
        if not google_enabled():
            abort(404)
        err = request.args.get("error")
        state = request.args.get("state", "")
        expected = session.pop("oauth_state", None)
        if err or not expected or not secrets.compare_digest(str(state), str(expected)):
            flash("Masuk dengan Google gagal, coba lagi", "error")
            return redirect(url_for("masuk"))
        code = request.args.get("code", "")
        try:
            token = _http_json(
                "https://oauth2.googleapis.com/token",
                method="POST",
                data={
                    "code": code,
                    "client_id": app.config["GOOGLE_CLIENT_ID"],
                    "client_secret": app.config["GOOGLE_CLIENT_SECRET"],
                    "redirect_uri": app.config["GOOGLE_REDIRECT_URI"],
                    "grant_type": "authorization_code",
                },
            )
            access = token.get("access_token", "")
            info = _http_json(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access}"},
            )
            email = normalize_email(info.get("email", ""))
            if not info.get("email_verified"):
                raise ValueError("unverified")
            sub = str(info.get("sub", ""))
            name = str(info.get("name") or email.split("@")[0])[:80] or "Pelanggan"
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                cust = c.execute("SELECT * FROM customers WHERE google_sub=?", (sub,)).fetchone()
                if not cust:
                    cust = c.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
                    if cust:
                        # Cegah pre-account-hijacking: hapus password jika akun email belum terverifikasi.
                        if not cust["email_verified"] and cust["password_hash"]:
                            c.execute(
                                "UPDATE customers SET google_sub=?,email_verified=1,password_hash=NULL WHERE id=?",
                                (sub, cust["id"]),
                            )
                        else:
                            c.execute(
                                "UPDATE customers SET google_sub=?,email_verified=1 WHERE id=?",
                                (sub, cust["id"]),
                            )
                        cust = c.execute("SELECT * FROM customers WHERE id=?", (cust["id"],)).fetchone()
                    else:
                        c.execute(
                            "INSERT INTO customers(email,name,password_hash,google_sub,email_verified,email_consent,created) VALUES (?,?,?,?,?,?,?)",
                            (email, name, None, sub, 1, 0, stamp(now())),
                        )
                        cust = c.execute("SELECT * FROM customers WHERE google_sub=?", (sub,)).fetchone()
            login_customer(cust["id"])
            return redirect(url_for("dasbor"))
        except Exception:
            flash("Masuk dengan Google gagal, coba lagi", "error")
            return redirect(url_for("masuk"))

    @app.route("/dasbor")
    @customer_only
    def dasbor():
        with db() as c:
            cust = get_customer(c, session["customer"])
            if not cust:
                session.clear()
                return redirect(url_for("masuk"))
            rows = customer_bookings(c, cust["id"])
            services = c.execute("SELECT * FROM services WHERE active=1").fetchall()
            barbers = c.execute("SELECT * FROM barbers WHERE active=1").fetchall()
        upcoming, history_rows = [], []
        for b in rows:
            if b["status"] == "scheduled" and parse(b["start"]) >= now():
                upcoming.append(b)
            else:
                history_rows.append(b)
        upcoming.sort(key=lambda x: x["start"])
        next_booking = upcoming[0] if upcoming else None
        remaining = None
        if next_booking:
            mins = int((parse(next_booking["start"]) - now()).total_seconds() // 60)
            if mins >= 1440:
                remaining = f"{mins // 1440} hari {(mins % 1440)//60} jam lagi"
            elif mins >= 60:
                remaining = f"{mins // 60} jam {mins % 60} menit lagi"
            else:
                remaining = f"{max(0, mins)} menit lagi"
        return render_template(
            "dasbor.html", cust=cust, upcoming=upcoming, history_rows=history_rows,
            next_booking=next_booking, remaining=remaining, services=services, barbers=barbers,
        )

    @app.route("/dasbor/booking", methods=["POST"])
    @customer_only
    def dasbor_booking():
        try:
            token = create_booking(
                app.config["DATABASE"], request.form, source="online",
                actor="pelanggan", customer_id=session["customer"],
            )
            flash("Booking berhasil dibuat.", "success")
            return redirect(url_for("booking_detail", token=token))
        except BookingError as e:
            flash(str(e), "error")
            return redirect(url_for("dasbor"))

    @app.route("/dasbor/profil", methods=["POST"])
    @customer_only
    def dasbor_profil():
        name = str(request.form.get("name", "")).strip()
        consent = 1 if request.form.get("email_consent") == "1" else 0
        if not 2 <= len(name) <= 80:
            flash("Nama harus terdiri dari 2 sampai 80 karakter.", "error")
            return redirect(url_for("dasbor"))
        with db() as c:
            c.execute("UPDATE customers SET name=?,email_consent=? WHERE id=?", (name, consent, session["customer"]))
        flash("Profil diperbarui.", "success")
        return redirect(url_for("dasbor"))

    @app.route("/dasbor/kirim-ulang", methods=["POST"])
    @customer_only
    def dasbor_kirim_ulang():
        cust = current_customer()
        if not cust:
            return redirect(url_for("masuk"))
        try:
            session["verify_email"] = cust["email"]
            issue_verification(cust["email"])
            flash("Jika email ini terdaftar, kode verifikasi sudah dikirim.", "success")
        except BookingError as e:
            flash(str(e), "error")
        return redirect(url_for("verifikasi"))

    @app.route("/keluar", methods=["POST"])
    def keluar():
        session.clear()
        flash("Anda sudah keluar.", "success")
        return redirect(url_for("home"))

    # --- Admin ---
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        with db() as c:
            if c.execute("SELECT 1 FROM admins").fetchone():
                return redirect(url_for("login"))
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)
        if request.method == "POST":
            try:
                email = normalize_email(request.form.get("email", ""))
            except BookingError as e:
                flash(str(e), "error")
                return render_template("auth.html", setup=True)
            password = request.form.get("password", "")
            if len(password) < 10:
                flash("Password minimal 10 karakter.", "error")
            else:
                with db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    if c.execute("SELECT 1 FROM admins").fetchone():
                        return redirect(url_for("login"))
                    admin_password_hash = generate_password_hash(password)
                    c.execute(
                        "INSERT INTO admins(email,password) VALUES (?,?)",
                        (email, admin_password_hash),
                    )
                session.clear()
                session["admin"] = email
                session["admin_credential"] = hashlib.sha256(admin_password_hash.encode()).hexdigest()
                flash("Akun admin dibuat. Sesuaikan pengaturan contoh sebelum digunakan.", "success")
                return redirect(url_for("admin_settings"))
        return render_template("auth.html", setup=True)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        with db() as c:
            if not c.execute("SELECT 1 FROM admins").fetchone():
                return redirect(url_for("setup"))
        if request.method == "POST":
            attempts = [t for t in session.get("attempts", []) if time.time() - t < 300]
            if len(attempts) >= 8:
                flash("Terlalu banyak percobaan. Tunggu 5 menit.", "error")
                return render_template("auth.html", setup=False), 429
            try:
                email = normalize_email(request.form.get("email", ""))
            except BookingError:
                email = ""
            with db() as c:
                a = c.execute("SELECT * FROM admins WHERE email=?", (email,)).fetchone()
            if a and check_password_hash(a["password"], request.form.get("password", "")):
                session.clear()
                session["admin"] = a["email"]
                session["admin_credential"] = hashlib.sha256(a["password"].encode()).hexdigest()
                return redirect(url_for("dashboard"))
            attempts.append(time.time())
            session["attempts"] = attempts
            flash("Email atau password tidak cocok.", "error")
        return render_template("auth.html", setup=False)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("home"))

    @app.route("/admin")
    @admin_only
    def dashboard():
        date = request.args.get("date", now().date().isoformat())
        try:
            parse(date)
        except BookingError:
            date = now().date().isoformat()
        with db() as c:
            rows = c.execute(
                "SELECT b.*,s.name service_name,k.name barber_name FROM bookings b JOIN services s ON s.id=b.service_id JOIN barbers k ON k.id=b.barber_id WHERE substr(b.start,1,10)=? ORDER BY b.start",
                (date,),
            ).fetchall()
        counts = {s: sum(b["status"] == s for b in rows) for s in STATUS}
        revenue = sum(b["paid"] or 0 for b in rows if b["status"] == "completed")
        return render_template("dashboard.html", rows=rows, date=date, counts=counts, revenue=revenue)

    @app.route("/admin/walkin", methods=["GET", "POST"])
    @admin_only
    def walkin():
        if request.method == "POST":
            try:
                token = create_booking(app.config["DATABASE"], request.form, source="walkin", actor="admin")
                flash("Pelanggan langsung berhasil masuk jadwal.", "success")
                return redirect(url_for("booking_detail", token=token))
            except BookingError as e:
                flash(str(e), "error")
        with db() as c:
            services = c.execute("SELECT * FROM services WHERE active=1").fetchall()
            barbers = c.execute("SELECT * FROM barbers WHERE active=1").fetchall()
        return render_template("walkin.html", services=services, barbers=barbers)

    @app.route("/admin/booking/<token>", methods=["POST"])
    @admin_only
    def admin_booking(token):
        try:
            action = request.form.get("action")
            if action in ("cancel", "reschedule"):
                change_booking(app.config["DATABASE"], token, action, request.form.get("start"), actor="admin")
            else:
                update_status(app.config["DATABASE"], token, action, request.form.get("paid"))
            flash("Booking berhasil diperbarui.", "success")
        except BookingError as e:
            flash(str(e), "error")
        return redirect(url_for("booking_detail", token=token))

    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_only
    def admin_settings():
        if request.method == "POST":
            try:
                action = request.form.get("action")
                with db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    if action == "shop":
                        name = request.form.get("shop_name", "").strip()
                        tol = request.form.get("tolerance", "").strip()
                        admin_email = normalize_email(request.form.get("admin_email"), required=False)
                        if not name or len(name) > 60:
                            raise BookingError("Nama tempat cukur wajib diisi, maksimal 60 karakter.")
                        if tol and (not tol.isdigit() or not 0 <= int(tol) <= 120):
                            raise BookingError("Toleransi harus 0-120 menit atau dikosongkan.")
                        for k, v in {
                            "shop_name": name, "address": request.form.get("address", "")[:200],
                            "admin_email": admin_email, "tolerance": tol, "sample_settings": "0",
                        }.items():
                            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))
                    elif action == "service":
                        name = request.form.get("name", "").strip()
                        duration = int(request.form.get("duration", 0))
                        price = int(request.form.get("price", -1))
                        sid = request.form.get("id")
                        if not name or len(name) > 60 or not 5 <= duration <= 180 or not 0 <= price <= 10000000:
                            raise BookingError("Isi layanan, durasi 5-180 menit, dan harga yang valid.")
                        if sid:
                            c.execute(
                                "UPDATE services SET name=?,duration=?,price=?,active=? WHERE id=?",
                                (name, duration, price, 1 if request.form.get("active") else 0, int(sid)),
                            )
                        else:
                            c.execute("INSERT INTO services(name,duration,price) VALUES (?,?,?)", (name, duration, price))
                    elif action == "barber":
                        name = request.form.get("name", "").strip()
                        if not name or len(name) > 60:
                            raise BookingError("Isi nama kapster, maksimal 60 karakter.")
                        if request.form.get("id"):
                            c.execute(
                                "UPDATE barbers SET name=?,active=? WHERE id=?",
                                (name, 1 if request.form.get("active") else 0, int(request.form["id"])),
                            )
                        else:
                            c.execute("INSERT INTO barbers(name) VALUES (?)", (name,))
                    elif action == "hours":
                        for day in range(7):
                            op = request.form.get(f"open_{day}", "")
                            cl = request.form.get(f"close_{day}", "")
                            bs = request.form.get(f"bs_{day}", "")
                            be = request.form.get(f"be_{day}", "")
                            closed = 1 if request.form.get(f"off_{day}") else 0
                            times = [parse("2000-01-01T" + x) for x in [op, cl]]
                            if times[0] >= times[1]:
                                raise BookingError("Jam tutup harus sesudah jam buka; jadwal lintas tengah malam belum didukung.")
                            if bool(bs) != bool(be):
                                raise BookingError("Isi kedua jam istirahat atau kosongkan keduanya.")
                            if bs and not (times[0] <= parse("2000-01-01T" + bs) < parse("2000-01-01T" + be) <= times[1]):
                                raise BookingError("Jam istirahat harus berada dalam jam kerja.")
                            c.execute(
                                "UPDATE hours SET opened=?,closed=?,break_start=?,break_end=?,is_closed=? WHERE weekday=?",
                                (op, cl, bs, be, closed, day),
                            )
                    elif action == "block":
                        start = parse(request.form.get("start"))
                        end = parse(request.form.get("end"))
                        bid = int(request.form.get("barber_id", 0)) or None
                        if end <= start:
                            raise BookingError("Akhir blokir harus setelah waktu mulai.")
                        reason = request.form.get("reason", "").strip()[:100]
                        if not reason:
                            raise BookingError("Isi alasan blokir.")
                        if c.execute(
                            "SELECT 1 FROM bookings WHERE (? IS NULL OR barber_id=?) AND status IN ('scheduled','arrived','serving') AND start<? AND end>?",
                            (bid, bid, stamp(end), stamp(start)),
                        ).fetchone():
                            raise BookingError("Ada booking aktif pada interval itu. Hubungi pelanggan dan atur ulang booking sebelum memblokir.")
                        c.execute("INSERT INTO blocks(barber_id,start,end,reason) VALUES (?,?,?,?)", (bid, stamp(start), stamp(end), reason))
                    elif action == "unblock":
                        c.execute("DELETE FROM blocks WHERE id=?", (int(request.form["id"]),))
                    else:
                        raise BookingError("Tindakan tidak dikenal.")
                flash("Pengaturan disimpan. Booking lama tidak diubah otomatis; periksa kalender jika jam kerja berubah.", "success")
            except (BookingError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_settings"))
        with db() as c:
            services = c.execute("SELECT * FROM services").fetchall()
            barbers = c.execute("SELECT * FROM barbers").fetchall()
            hours = c.execute("SELECT * FROM hours ORDER BY weekday").fetchall()
            blocks = c.execute(
                "SELECT x.*,k.name barber_name FROM blocks x LEFT JOIN barbers k ON k.id=x.barber_id ORDER BY start"
            ).fetchall()
        return render_template("settings.html", services=services, barbers=barbers, hours=hours, blocks=blocks)

    @app.route("/admin/messages", methods=["GET", "POST"])
    @admin_only
    def messages():
        if request.method == "POST":
            try:
                n = process_messages(app.config["DATABASE"], app.config)
                flash(f"{n} pesan jatuh tempo diproses. Periksa status di bawah.", "success")
            except Exception:
                flash("Pemrosesan tertunda; coba lagi setelah beberapa saat.", "error")
            return redirect(url_for("messages"))
        with db() as c:
            rows = c.execute(
                "SELECT m.*,b.code,b.name,b.email FROM messages m JOIN bookings b ON b.id=m.booking_id ORDER BY m.id DESC LIMIT 80"
            ).fetchall()
            verifs = c.execute(
                "SELECT id,email,kind,created as due,status,preview,error FROM verification_messages ORDER BY id DESC LIMIT 40"
            ).fetchall()
        combined = [dict(r) for r in rows] + [dict(r) | {"code": "AKUN", "name": "Pelanggan"} for r in verifs]
        combined.sort(key=lambda x: (str(x.get("processed_at") or x.get("due") or ""), x.get("id", 0)), reverse=True)
        return render_template("messages.html", rows=combined[:120])

    @app.route("/admin/analytics")
    @admin_only
    def analytics():
        with db() as c:
            data = insights(c)
            promos = c.execute("SELECT * FROM promos ORDER BY date DESC LIMIT 20").fetchall()
        return render_template("analytics.html", data=data, promos=promos)

    @app.route("/admin/promo", methods=["POST"])
    @admin_only
    def promo():
        try:
            with db() as c:
                if request.form.get("action") == "disable":
                    c.execute("UPDATE promos SET active=0 WHERE id=?", (int(request.form["id"]),))
                else:
                    date = request.form.get("date", "")
                    start = request.form.get("start_time", "")
                    end = request.form.get("end_time", "")
                    label = request.form.get("label", "").strip()
                    if (
                        parse(date + "T" + start) < now()
                        or parse(date + "T" + end) <= parse(date + "T" + start)
                        or not 3 <= len(label) <= 100
                    ):
                        raise BookingError("Isi tanggal, rentang jam mendatang, dan keterangan promo yang valid.")
                    c.execute(
                        "INSERT INTO promos(date,start_time,end_time,label) VALUES (?,?,?,?)",
                        (date, start, end, label),
                    )
            flash("Informasi promo diperbarui. Harga akhir tetap dicatat admin saat pembayaran.", "success")
        except (BookingError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("analytics"))

    @app.errorhandler(404)
    def not_found(e):
        return (render_template("error.html", title="Halaman tidak ditemukan", message="Periksa kembali tautan booking atau kembali ke beranda."), 404)

    @app.errorhandler(400)
    def bad_request(e):
        return (render_template("error.html", title="Permintaan belum dapat diproses", message=e.description), 400)

    return app


if __name__ == "__main__":
    app = create_app()

    def worker():
        while True:
            try:
                process_messages(app.config["DATABASE"], app.config)
            except Exception:
                logging.exception("Outbox tertunda")
            time.sleep(15)

    threading.Thread(target=worker, daemon=True).start()
    print("\nRuang Pangkas: http://127.0.0.1:5000 | Buat akun admin: /setup\n")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
