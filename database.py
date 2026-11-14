import sqlite3
import time
from pathlib import Path
from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class TursoDatabase:
    url: str
    auth_token: str = field(repr=False)


class ResultRow:
    """SQLite-style named and positional access for the libSQL driver."""
    def __init__(self, columns, values):
        self._columns = columns
        self._values = tuple(values)

    def __getitem__(self, key):
        return self._values[self._columns.index(key)] if isinstance(key, str) else self._values[key]

    def keys(self):
        return list(self._columns)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class RemoteCursor:
    def __init__(self, cursor):
        # libSQL derives lastrowid from the connection: snapshot it before any
        # subsequent INSERT (for example history) changes the connection value.
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount
        columns = [column[0] for column in (cursor.description or ())]
        self._rows = iter(ResultRow(columns, row) for row in (cursor.fetchall() or []))

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return self._rows


class RemoteConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, parameters=()):
        deadline = time.monotonic() + 15
        while True:
            try:
                return RemoteCursor(self._connection.execute(sql, parameters))
            except ValueError as error:
                message = str(error).lower()
                # Retry only a rejected lock acquisition, never an INSERT or an
                # uncertain network result. Native busy waiting may retain the GIL.
                if ("locked" in message or "sqlite_busy" in message) and sql.strip().upper() == "BEGIN IMMEDIATE" and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                if any(word in message for word in ("constraint", "satu akun admin", "foreign key")):
                    raise sqlite3.IntegrityError("Operasi ditolak oleh aturan integritas database.") from None
                raise sqlite3.DatabaseError("Operasi database Turso gagal.") from None

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def executescript(self, script):
        # complete_statement preserves trigger bodies with internal semicolons.
        statement = ""
        for char in script:
            statement += char
            if char == ";" and sqlite3.complete_statement(statement):
                self.execute(statement)
                statement = ""
        if statement.strip():
            self.execute(statement)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                try:
                    self.commit()
                except Exception:
                    self.rollback()
                    raise
            else:
                self.rollback()
        finally:
            self.close()


def _open_remote(target):
    if not target.auth_token.strip():
        raise RuntimeError("Isi TURSO_AUTH_TOKEN di konfigurasi pribadi.")
    if not target.url.startswith("libsql://") or not target.url.endswith(".turso.io"):
        raise RuntimeError("TURSO_DATABASE_URL harus berupa libsql://...turso.io.")
    try:
        import libsql
    except ImportError:
        raise RuntimeError("Driver Turso belum terpasang. Jalankan pip install -r requirements.txt.") from None
    db = RemoteConnection(libsql.connect(database=target.url, auth_token=target.auth_token, timeout=0))
    try:
        db.execute("PRAGMA foreign_keys=ON")
    except Exception:
        db.close()
        raise
    return db


def connect(path):
    if isinstance(path, TursoDatabase):
        return _open_remote(path)
    db = sqlite3.connect(str(path), timeout=15, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def init_db(path):
    if not isinstance(path, TursoDatabase):
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
        if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] > 1:
            raise RuntimeError("Database memiliki lebih dari satu admin. Tinjau akun sebelum melanjutkan; tidak ada akun yang dihapus otomatis.")
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS admins_single_account
        BEFORE INSERT ON admins WHEN (SELECT COUNT(*) FROM admins) >= 1
        BEGIN
            SELECT RAISE(ABORT, 'Hanya satu akun admin diperbolehkan');
        END;
        """)
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookings'"
        ).fetchone():
            cols = _columns(db, "bookings")
            if "customer_id" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
        db.execute("CREATE INDEX IF NOT EXISTS booking_customer ON bookings(customer_id,start)")
        db.execute("BEGIN IMMEDIATE")
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
