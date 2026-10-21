from datetime import datetime,timedelta,timezone
import re,secrets
from database import connect,settings

ACTIVE=('scheduled','arrived','serving')
RESERVED=('scheduled','arrived','serving','completed')
class BookingError(ValueError):pass

def now():return datetime.now(timezone(timedelta(hours=7))).replace(tzinfo=None,microsecond=0)
def stamp(dt):return dt.isoformat(timespec='seconds')
def parse(value):
 try:
  dt=datetime.fromisoformat(str(value))
  if dt.tzinfo is not None:raise ValueError()
  return dt.replace(microsecond=0)
 except (ValueError,TypeError):raise BookingError('Tanggal atau jam tidak valid.')
def phone_number(value,required=True):
 value=re.sub(r'[\s()+-]','',str(value or ''))
 if not value and not required:return ''
 if value.startswith('0'):value='62'+value[1:]
 if not re.fullmatch(r'62[0-9]{8,12}',value):raise BookingError('Isi nomor WA Indonesia yang valid, misalnya 081234567890.')
 return value

def record(db,bid,action,actor,old=None,new=None):
 db.execute('INSERT INTO history(booking_id,action,actor,old_start,new_start,created) VALUES (?,?,?,?,?,?)',(bid,action,actor,old,new,stamp(now())))

def validate_slot(db,barber_id,start,duration,exclude=None,at=None):
 at=at or now();end=start+timedelta(minutes=duration)
 if start<at:raise BookingError('Pilih waktu yang belum lewat.')
 if start>at+timedelta(days=60):raise BookingError('Booking tersedia maksimal 60 hari ke depan.')
 if start.second or start.minute%15:raise BookingError('Pilih jam dari pilihan slot yang tersedia.')
 if not db.execute('SELECT 1 FROM barbers WHERE id=? AND active=1',(barber_id,)).fetchone():raise BookingError('Kapster tidak tersedia.')
 h=db.execute('SELECT * FROM hours WHERE weekday=?',(start.weekday(),)).fetchone()
 if not h or h['is_closed']:raise BookingError('Tempat cukur tutup pada hari ini.')
 day=start.date().isoformat();opened=parse(day+'T'+h['opened']);closed=parse(day+'T'+h['closed'])
 if start<opened or end>closed:raise BookingError('Durasi layanan melewati jam kerja.')
 if h['break_start'] and start<parse(day+'T'+h['break_end']) and end>parse(day+'T'+h['break_start']):raise BookingError('Waktu ini bertepatan dengan istirahat.')
 if db.execute('SELECT 1 FROM blocks WHERE (barber_id=? OR barber_id IS NULL) AND start<? AND end>?',(barber_id,stamp(end),stamp(start))).fetchone():raise BookingError('Waktu ini sedang diblokir admin.')
 if db.execute("SELECT 1 FROM bookings WHERE barber_id=? AND id!=? AND status IN ('scheduled','arrived','serving','completed') AND start<? AND end>?",(barber_id,exclude or -1,stamp(end),stamp(start))).fetchone():raise BookingError('Slot baru saja terambil. Silakan pilih jam lain.')
 return end

def slots(db,service_id,barber_id,date,exclude=None,at=None,duration=None):
 try:day=datetime.strptime(date,'%Y-%m-%d')
 except (ValueError,TypeError):raise BookingError('Tanggal tidak valid.')
 service=db.execute('SELECT * FROM services WHERE id=? AND active=1',(service_id,)).fetchone()
 if not service:raise BookingError('Layanan tidak tersedia.')
 duration=duration or service['duration'];h=db.execute('SELECT * FROM hours WHERE weekday=?',(day.weekday(),)).fetchone()
 if not h or h['is_closed']:return []
 t=parse(date+'T'+h['opened']);stop=parse(date+'T'+h['closed']);result=[]
 # Grid fixed to quarter-hour, including when opening hour was adjusted.
 t=t.replace(minute=(t.minute//15)*15,second=0)
 while t<stop:
  try:
   end=validate_slot(db,barber_id,t,duration,exclude,at);result.append({'value':stamp(t),'label':t.strftime('%H:%M'),'end':end.strftime('%H:%M')})
  except BookingError:pass
  t+=timedelta(minutes=15)
 return result

def queue(db,bid,kind,at=None):
 at=at or now();b=db.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone()
 db.execute("UPDATE messages SET status='obsolete' WHERE booking_id=? AND status='pending'",(bid,))
 if not b['phone'] or not b['wa_consent']:return
 db.execute('INSERT OR IGNORE INTO messages(booking_id,version,kind,due) VALUES (?,?,?,?)',(bid,b['version'],kind,stamp(at)))
 if kind!='cancellation' and b['source']=='online' and parse(b['start'])-at>=timedelta(hours=2):
  db.execute('INSERT OR IGNORE INTO messages(booking_id,version,kind,due) VALUES (?,?,?,?)',(bid,b['version'],'reminder',stamp(parse(b['start'])-timedelta(hours=2))))

def create_booking(path,data,source='online',actor='pelanggan'):
 name=str(data.get('name','')).strip()
 if not 2<=len(name)<=80:raise BookingError('Nama harus terdiri dari 2 sampai 80 karakter.')
 phone=phone_number(data.get('phone'),required=source=='online')
 try:service_id=int(data['service_id']);barber_id=int(data['barber_id'])
 except (ValueError,KeyError,TypeError):raise BookingError('Pilih layanan dan kapster.')
 start=parse(data.get('start'));at=now()
 with connect(path) as db:
  db.execute('BEGIN IMMEDIATE')
  s=db.execute('SELECT * FROM services WHERE id=? AND active=1',(service_id,)).fetchone()
  if not s:raise BookingError('Layanan tidak tersedia.')
  end=validate_slot(db,barber_id,start,s['duration'],at=at)
  token=secrets.token_urlsafe(32);code='RP-'+secrets.token_hex(4).upper()
  consent=1 if data.get('wa_consent') in ('on','1',True) else 0
  cur=db.execute('INSERT INTO bookings(token,code,name,phone,service_id,barber_id,start,end,duration,price,source,created,wa_consent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(token,code,name,phone,service_id,barber_id,stamp(start),stamp(end),s['duration'],s['price'],source,stamp(at),consent))
  record(db,cur.lastrowid,'booking_dibuat',actor,new=stamp(start));queue(db,cur.lastrowid,'confirmation',at)
  return token

def get_booking(db,token):
 return db.execute('SELECT b.*,s.name service_name,k.name barber_name FROM bookings b JOIN services s ON b.service_id=s.id JOIN barbers k ON b.barber_id=k.id WHERE b.token=?',(token,)).fetchone()

def change_booking(path,token,action,start=None,actor='pelanggan'):
 with connect(path) as db:
  db.execute('BEGIN IMMEDIATE');b=get_booking(db,token)
  if not b:raise BookingError('Booking tidak ditemukan.')
  if b['status']!='scheduled':raise BookingError('Hanya booking terjadwal yang dapat diubah atau dibatalkan.')
  at=now()
  if actor=='pelanggan' and parse(b['start'])-at<timedelta(hours=1):raise BookingError('Batas satu jam sudah lewat. Hubungi admin untuk bantuan.')
  if action=='reschedule':
   target=parse(start)
   if stamp(target)==b['start']:raise BookingError('Pilih jadwal yang berbeda.')
   end=validate_slot(db,b['barber_id'],target,b['duration'],exclude=b['id'],at=at)
   db.execute('UPDATE bookings SET start=?,end=?,version=version+1 WHERE id=?',(stamp(target),stamp(end),b['id']))
   record(db,b['id'],'jadwal_diubah',actor,b['start'],stamp(target));queue(db,b['id'],'reschedule',at)
  elif action=='cancel':
   db.execute("UPDATE bookings SET status='cancelled',version=version+1 WHERE id=?",(b['id'],))
   record(db,b['id'],'dibatalkan',actor,b['start']);queue(db,b['id'],'cancellation',at)
  else:raise BookingError('Tindakan tidak dikenal.')

def update_status(path,token,target,paid=None):
 with connect(path) as db:
  db.execute('BEGIN IMMEDIATE');b=get_booking(db,token)
  if not b:raise BookingError('Booking tidak ditemukan.')
  at=now();valid={'scheduled':['arrived','no_show'],'arrived':['serving'],'serving':['completed']}
  if target not in valid.get(b['status'],[]):raise BookingError('Perubahan status tidak sesuai urutan pelayanan.')
  if parse(b['start']).date()>at.date():raise BookingError('Pelayanan belum tiba harinya.')
  if target=='no_show':
   tol=settings(db).get('tolerance','')
   if not tol:raise BookingError('Atur toleransi keterlambatan terlebih dahulu.')
   if at<parse(b['start'])+timedelta(minutes=int(tol)):raise BookingError('Belum melewati batas toleransi kedatangan.')
  if target=='serving' and db.execute("SELECT 1 FROM bookings WHERE barber_id=? AND status='serving' AND id!=?",(b['barber_id'],b['id'])).fetchone():raise BookingError('Kapster masih melayani pelanggan lain.')
  column={'arrived':'arrived_at','serving':'started_at','completed':'completed_at'}.get(target)
  db.execute('UPDATE bookings SET status=? WHERE id=?',(target,b['id']))
  if column:db.execute(f'UPDATE bookings SET {column}=? WHERE id=?',(stamp(at),b['id']))
  if target=='completed':
   try:amount=int(paid)
   except (ValueError,TypeError):raise BookingError('Isi nilai pembayaran dalam rupiah.')
   if not 0<=amount<=10000000:raise BookingError('Nilai pembayaran tidak valid.')
   db.execute('UPDATE bookings SET paid=? WHERE id=?',(amount,b['id']))
  db.execute("UPDATE messages SET status='obsolete' WHERE booking_id=? AND kind='reminder' AND status='pending'",(b['id'],))
  record(db,b['id'],target,'admin')
