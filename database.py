import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS services (id INTEGER PRIMARY KEY,name TEXT NOT NULL,duration INTEGER NOT NULL CHECK(duration>0),price INTEGER NOT NULL CHECK(price>=0),active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS barbers (id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hours (weekday INTEGER PRIMARY KEY,opened TEXT NOT NULL,closed TEXT NOT NULL,break_start TEXT NOT NULL,break_end TEXT NOT NULL,is_closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS blocks (id INTEGER PRIMARY KEY,barber_id INTEGER REFERENCES barbers(id),start TEXT NOT NULL,end TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings (
 id INTEGER PRIMARY KEY,token TEXT UNIQUE NOT NULL,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL,
 service_id INTEGER NOT NULL REFERENCES services(id),barber_id INTEGER NOT NULL REFERENCES barbers(id),
 start TEXT NOT NULL,end TEXT NOT NULL,duration INTEGER NOT NULL,price INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','arrived','serving','completed','cancelled','no_show')),
 source TEXT NOT NULL CHECK(source IN ('online','walkin')),created TEXT NOT NULL,arrived_at TEXT,started_at TEXT,completed_at TEXT,paid INTEGER,
 version INTEGER NOT NULL DEFAULT 1,wa_consent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS booking_slots ON bookings(barber_id,start,end,status);
CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY,booking_id INTEGER NOT NULL REFERENCES bookings(id),action TEXT NOT NULL,actor TEXT NOT NULL,old_start TEXT,new_start TEXT,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY,booking_id INTEGER NOT NULL REFERENCES bookings(id),version INTEGER NOT NULL,kind TEXT NOT NULL,due TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',preview TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',provider_id TEXT,processed_at TEXT,UNIQUE(booking_id,version,kind));
CREATE TABLE IF NOT EXISTS promos (id INTEGER PRIMARY KEY,date TEXT NOT NULL,start_time TEXT NOT NULL,end_time TEXT NOT NULL,label TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
"""
class Connection(sqlite3.Connection):
 def __exit__(self,*args):
  try:return super().__exit__(*args)
  finally:self.close()

def connect(path):
 db=sqlite3.connect(str(path),timeout=15,factory=Connection);db.row_factory=sqlite3.Row
 db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=15000');return db

def init_db(path):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 with connect(path) as db:
  db.executescript(SCHEMA)
  defaults={'shop_name':'Ruang Pangkas','address':'Alamat belum diatur','admin_phone':'','tolerance':'','sample_settings':'1'}
  db.executemany('INSERT OR IGNORE INTO settings VALUES (?,?)',defaults.items())
  if not db.execute('SELECT 1 FROM services').fetchone():
   db.executemany('INSERT INTO services(name,duration,price) VALUES (?,?,?)',[('Potong rambut',30,35000),('Potong + keramas',45,50000),('Rapikan jenggot',15,20000)])
  if not db.execute('SELECT 1 FROM barbers').fetchone():db.executemany('INSERT INTO barbers(name) VALUES (?)',[('Kapster 1',),('Kapster 2',)])
  for day in range(7):db.execute('INSERT OR IGNORE INTO hours VALUES (?,?,?,?,?,0)',(day,'09:00','20:00','12:00','13:00'))

def settings(db):return {r['key']:r['value'] for r in db.execute('SELECT * FROM settings')}
