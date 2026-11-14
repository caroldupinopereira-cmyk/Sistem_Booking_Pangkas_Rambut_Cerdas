import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers (
 id INTEGER PRIMARY KEY,
 email TEXT UNIQUE NOT NULL,
 name TEXT NOT NULL,
 password_hash TEXT,
 google_sub TEXT UNIQUE,
 email_verified INTEGER NOT NULL DEFAULT 0,
 email_consent INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_codes (
 id INTEGER PRIMARY KEY,
 email TEXT NOT NULL,
 code_hash TEXT NOT NULL,
 expires TEXT NOT NULL,
 used INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS verification_email ON verification_codes(email,created);
CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY,name TEXT NOT NULL,duration INTEGER NOT NULL CHECK(duration>0),price INTEGER NOT NULL CHECK(price>=0),active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS barbers (id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hours (weekday INTEGER PRIMARY KEY,opened TEXT NOT NULL,closed TEXT NOT NULL,break_start TEXT NOT NULL,break_end TEXT NOT NULL,is_closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS blocks (id INTEGER PRIMARY KEY,barber_id INTEGER REFERENCES barbers(id),start TEXT NOT NULL,end TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings (
 id INTEGER PRIMARY KEY,token TEXT UNIQUE NOT NULL,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,email TEXT NOT NULL,
 service_id INTEGER NOT NULL REFERENCES services(id),barber_id INTEGER NOT NULL REFERENCES barbers(id),
 start TEXT NOT NULL,end TEXT NOT NULL,duration INTEGER NOT NULL,price INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','arrived','serving','completed','cancelled','no_show')),
 source TEXT NOT NULL CHECK(source IN ('online','walkin')),created TEXT NOT NULL,arrived_at TEXT,started_at TEXT,completed_at TEXT,paid INTEGER,
 version INTEGER NOT NULL DEFAULT 1,email_consent INTEGER NOT NULL DEFAULT 0,customer_id INTEGER REFERENCES customers(id));
CREATE INDEX IF NOT EXISTS booking_slots ON bookings(barber_id,start,end,status);
CREATE INDEX IF NOT EXISTS booking_customer ON bookings(customer_id,start);
CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY,booking_id INTEGER NOT NULL REFERENCES bookings(id),action TEXT NOT NULL,actor TEXT NOT NULL,old_start TEXT,new_start TEXT,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY,booking_id INTEGER NOT NULL REFERENCES bookings(id),version INTEGER NOT NULL,kind TEXT NOT NULL,due TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',preview TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',provider_id TEXT,processed_at TEXT,UNIQUE(booking_id,version,kind));
CREATE TABLE IF NOT EXISTS verification_messages (id INTEGER PRIMARY KEY,email TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'verification',created TEXT NOT NULL,status TEXT NOT NULL,preview TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS promos (id INTEGER PRIMARY KEY,date TEXT NOT NULL,start_time TEXT NOT NULL,end_time TEXT NOT NULL,label TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
"""


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    db = sqlite3.connect(str(path), timeout=15, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookings'"
        ).fetchone():
            cols = _columns(db, "bookings")
            if "phone" in cols or "wa_consent" in cols or "email" not in cols:
                raise RuntimeError(
                    "Skema database lama terdeteksi (kolom WhatsApp/nomor). "
                    "Hapus file instance/barbershop.sqlite3 lalu jalankan aplikasi kembali. "
                    "File tersebut tidak dihapus otomatis."
                )
            if "customer_id" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='admins'"
        ).fetchone():
            cols = _columns(db, "admins")
            if "username" in cols or "email" not in cols:
                raise RuntimeError(
                    "Skema database lama terdeteksi (kolom username admin). "
                    "Hapus file instance/barbershop.sqlite3 lalu jalankan aplikasi kembali. "
                    "File tersebut tidak dihapus otomatis."
                )
        # Buang sisa fitur login pelanggan versi lama bila ada.
        for old in ("login_codes",):
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (old,)
            ).fetchone():
                db.execute(f"DROP TABLE {old}")
        db.executescript(SCHEMA)
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookings'"
        ).fetchone():
            cols = _columns(db, "bookings")
            if "customer_id" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
        db.execute("CREATE INDEX IF NOT EXISTS booking_customer ON bookings(customer_id,start)")
        defaults = {
            "shop_name": "Ruang Pangkas",
            "address": "Alamat belum diatur",
            "admin_email": "",
            "tolerance": "",
            "sample_settings": "1",
        }
        db.executemany("INSERT OR IGNORE INTO settings VALUES (?,?)", defaults.items())
        if not db.execute("SELECT 1 FROM services").fetchone():
            db.executemany(
                "INSERT INTO services(name,duration,price) VALUES (?,?,?)",
                [
                    ("Potong rambut", 30, 35000),
                    ("Potong + keramas", 45, 50000),
                    ("Rapikan jenggot", 15, 20000),
                ],
            )
        if not db.execute("SELECT 1 FROM barbers").fetchone():
            db.executemany(
                "INSERT INTO barbers(name) VALUES (?)", [("Kapster 1",), ("Kapster 2",)]
            )
        for day in range(7):
            db.execute(
                "INSERT OR IGNORE INTO hours VALUES (?,?,?,?,?,0)",
                (day, "09:00", "20:00", "12:00", "13:00"),
            )


def settings(db):
    return {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings")}
