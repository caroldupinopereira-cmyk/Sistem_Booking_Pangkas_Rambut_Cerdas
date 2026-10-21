from pathlib import Path
from functools import wraps
from datetime import timedelta
import os,secrets,threading,time,logging
from flask import Flask,render_template,request,redirect,url_for,session,flash,jsonify,abort
from werkzeug.security import generate_password_hash,check_password_hash
from database import connect,init_db,settings
from booking import BookingError,now,stamp,parse,slots,create_booking,get_booking,change_booking,update_status,phone_number
from messaging import process_messages
from analytics import insights

ROOT=Path(__file__).resolve().parent
STATUS={'scheduled':'Terjadwal','arrived':'Sudah datang','serving':'Dilayani','completed':'Selesai','cancelled':'Dibatalkan','no_show':'No-show'}
DAYS=['Senin','Selasa','Rabu','Kamis','Jumat','Sabtu','Minggu']

def create_app(test_config=None):
 app=Flask(__name__)
 env={}
 if (ROOT/'.env').exists():
  for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
   if '=' in line and not line.lstrip().startswith('#'):
    key,value=line.split('=',1);env[key.strip()]=value.strip().strip('"').strip("'")
 instance=ROOT/'instance';instance.mkdir(exist_ok=True)
 secretfile=instance/'secret.key'
 if not secretfile.exists():secretfile.write_text(secrets.token_hex(32),encoding='utf-8')
 app.config.update(SECRET_KEY=secretfile.read_text().strip(),DATABASE=str(instance/'barbershop.sqlite3'),WA_MODE='demo',PUBLIC_BASE_URL='http://127.0.0.1:5000',MAX_CONTENT_LENGTH=65536,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax')
 app.config.update(env)
 for key in ['WA_MODE','WA_TOKEN','WA_API_VERSION','WA_PHONE_NUMBER_ID','WA_LANGUAGE','PUBLIC_BASE_URL','WA_TEMPLATE_CONFIRMATION','WA_TEMPLATE_RESCHEDULE','WA_TEMPLATE_CANCELLATION','WA_TEMPLATE_REMINDER']:
  if key in os.environ:app.config[key]=os.environ[key]
 if test_config:app.config.update(test_config)
 init_db(app.config['DATABASE'])
 with connect(app.config['DATABASE']) as db:db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('tracking_started',stamp(now())))
 def db():return connect(app.config['DATABASE'])
 def admin_only(f):
  @wraps(f)
  def inner(*a,**kw):
   if not session.get('admin'):return redirect(url_for('login'))
   return f(*a,**kw)
  return inner
 def csrf():
  if 'csrf' not in session:session['csrf']=secrets.token_urlsafe(32)
  return session['csrf']
 @app.before_request
 def protect():
  if request.method=='POST' and not secrets.compare_digest(str(request.form.get('csrf','')),str(session.get('csrf','missing'))):abort(400,description='Form kedaluwarsa. Muat ulang halaman dan coba lagi.')
 @app.after_request
 def headers(response):
  response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['Referrer-Policy']='no-referrer'
  response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  if request.path.startswith(('/admin','/booking','/login','/setup')):response.headers['Cache-Control']='no-store'
  return response
 @app.context_processor
 def common():
  with db() as c:cfg=settings(c);has_admin=bool(c.execute('SELECT 1 FROM admins').fetchone())
  return dict(shop=cfg,status_labels=STATUS,days=DAYS,csrf=csrf,is_admin=bool(session.get('admin')),has_admin=has_admin,wa_mode=app.config['WA_MODE'],today=now().date().isoformat())
 @app.template_filter('rupiah')
 def rupiah(value):return 'Rp '+f'{int(value or 0):,}'.replace(',','.')
 @app.template_filter('when')
 def when(value):
  if not value:return '-'
  dt=parse(value);return f'{DAYS[dt.weekday()]}, {dt:%d/%m/%Y · %H:%M}'
 @app.route('/')
 def home():
  with db() as c:
   services=c.execute('SELECT * FROM services WHERE active=1').fetchall();barbers=c.execute('SELECT * FROM barbers WHERE active=1').fetchall();promos=c.execute('SELECT * FROM promos WHERE active=1 AND date>=? ORDER BY date LIMIT 3',(now().date().isoformat(),)).fetchall()
  return render_template('home.html',services=services,barbers=barbers,promos=promos)
 @app.route('/book',methods=['POST'])
 def book():
  try:
   token=create_booking(app.config['DATABASE'],request.form)
   return redirect(url_for('booking_detail',token=token,created=1))
  except BookingError as e:flash(str(e),'error');return redirect(url_for('home')+'#booking')
 @app.route('/api/slots')
 def available():
  try:
   with db() as c:
    token=request.args.get('token');b=get_booking(c,token) if token else None
    if token and (not b or b['status']!='scheduled'):raise BookingError('Booking tidak tersedia untuk diubah.')
    if b and parse(b['start'])-now()<timedelta(hours=1) and not session.get('admin'):raise BookingError('Batas perubahan sudah lewat. Hubungi admin.')
    service=int(b['service_id'] if b else request.args.get('service',0));barber=int(b['barber_id'] if b else request.args.get('barber',0))
    result=slots(c,service,barber,request.args.get('date'),exclude=b['id'] if b else None,duration=b['duration'] if b else None)
   return jsonify(slots=result)
  except (BookingError,ValueError,TypeError) as e:return jsonify(error=str(e)),400
 @app.route('/booking/<token>')
 def booking_detail(token):
  with db() as c:
   b=get_booking(c,token)
   if not b:abort(404)
   history=c.execute('SELECT * FROM history WHERE booking_id=? ORDER BY id DESC',(b['id'],)).fetchall()
  editable=b['status']=='scheduled' and parse(b['start'])-now()>=timedelta(hours=1)
  return render_template('booking.html',b=b,history=history,editable=editable)
 @app.route('/booking/<token>/change',methods=['POST'])
 def change(token):
  try:change_booking(app.config['DATABASE'],token,request.form.get('action'),request.form.get('start'));flash('Perubahan booking berhasil disimpan.','success')
  except BookingError as e:flash(str(e),'error')
  return redirect(url_for('booking_detail',token=token))
 @app.route('/setup',methods=['GET','POST'])
 def setup():
  with db() as c:
   if c.execute('SELECT 1 FROM admins').fetchone():return redirect(url_for('login'))
  if request.remote_addr not in ('127.0.0.1','::1'):abort(403)
  if request.method=='POST':
   username=request.form.get('username','').strip();password=request.form.get('password','')
   if len(username)<3 or len(password)<10:flash('Username minimal 3 karakter, password minimal 10 karakter.','error')
   else:
    with db() as c:
     c.execute('BEGIN IMMEDIATE')
     if c.execute('SELECT 1 FROM admins').fetchone():return redirect(url_for('login'))
     c.execute('INSERT INTO admins(username,password) VALUES (?,?)',(username,generate_password_hash(password)))
    session.clear();session['admin']=username;flash('Akun admin dibuat. Sesuaikan pengaturan contoh sebelum digunakan.','success');return redirect(url_for('admin_settings'))
  return render_template('auth.html',setup=True)
 @app.route('/login',methods=['GET','POST'])
 def login():
  with db() as c:
   if not c.execute('SELECT 1 FROM admins').fetchone():return redirect(url_for('setup'))
  if request.method=='POST':
   attempts=session.get('attempts',[]);attempts=[t for t in attempts if time.time()-t<300]
   if len(attempts)>=8:flash('Terlalu banyak percobaan. Tunggu 5 menit.','error');return render_template('auth.html',setup=False),429
   with db() as c:a=c.execute('SELECT * FROM admins WHERE username=?',(request.form.get('username',''),)).fetchone()
   if a and check_password_hash(a['password'],request.form.get('password','')):
    session.clear();session['admin']=a['username'];return redirect(url_for('dashboard'))
   attempts.append(time.time());session['attempts']=attempts;flash('Username atau password tidak cocok.','error')
  return render_template('auth.html',setup=False)
 @app.route('/logout',methods=['POST'])
 def logout():session.clear();return redirect(url_for('home'))
 @app.route('/admin')
 @admin_only
 def dashboard():
  date=request.args.get('date',now().date().isoformat())
  try:parse(date)
  except BookingError:date=now().date().isoformat()
  with db() as c:
   rows=c.execute('SELECT b.*,s.name service_name,k.name barber_name FROM bookings b JOIN services s ON s.id=b.service_id JOIN barbers k ON k.id=b.barber_id WHERE substr(b.start,1,10)=? ORDER BY b.start',(date,)).fetchall()
  counts={s:sum(b['status']==s for b in rows) for s in STATUS};revenue=sum(b['paid'] or 0 for b in rows if b['status']=='completed')
  return render_template('dashboard.html',rows=rows,date=date,counts=counts,revenue=revenue)
 @app.route('/admin/walkin',methods=['GET','POST'])
 @admin_only
 def walkin():
  if request.method=='POST':
   try:
    token=create_booking(app.config['DATABASE'],request.form,source='walkin',actor='admin');flash('Pelanggan langsung berhasil masuk jadwal.','success');return redirect(url_for('booking_detail',token=token))
   except BookingError as e:flash(str(e),'error')
  with db() as c:services=c.execute('SELECT * FROM services WHERE active=1').fetchall();barbers=c.execute('SELECT * FROM barbers WHERE active=1').fetchall()
  return render_template('walkin.html',services=services,barbers=barbers)
 @app.route('/admin/booking/<token>',methods=['POST'])
 @admin_only
 def admin_booking(token):
  try:
   action=request.form.get('action')
   if action in ('cancel','reschedule'):change_booking(app.config['DATABASE'],token,action,request.form.get('start'),actor='admin')
   else:update_status(app.config['DATABASE'],token,action,request.form.get('paid'))
   flash('Booking berhasil diperbarui.','success')
  except BookingError as e:flash(str(e),'error')
  return redirect(url_for('booking_detail',token=token))
 @app.route('/admin/settings',methods=['GET','POST'])
 @admin_only
 def admin_settings():
  if request.method=='POST':
   try:
    action=request.form.get('action')
    with db() as c:
     c.execute('BEGIN IMMEDIATE')
     if action=='shop':
      name=request.form.get('shop_name','').strip();tol=request.form.get('tolerance','').strip();phone=phone_number(request.form.get('admin_phone'),required=False)
      if not name or len(name)>60:raise BookingError('Nama tempat cukur wajib diisi, maksimal 60 karakter.')
      if tol and (not tol.isdigit() or not 0<=int(tol)<=120):raise BookingError('Toleransi harus 0-120 menit atau dikosongkan.')
      for k,v in {'shop_name':name,'address':request.form.get('address','')[:200],'admin_phone':phone,'tolerance':tol,'sample_settings':'0'}.items():c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,v))
     elif action=='service':
      name=request.form.get('name','').strip();duration=int(request.form.get('duration',0));price=int(request.form.get('price',-1));sid=request.form.get('id')
      if not name or len(name)>60 or not 5<=duration<=180 or not 0<=price<=10000000:raise BookingError('Isi layanan, durasi 5-180 menit, dan harga yang valid.')
      if sid:c.execute('UPDATE services SET name=?,duration=?,price=?,active=? WHERE id=?',(name,duration,price,1 if request.form.get('active') else 0,int(sid)))
      else:c.execute('INSERT INTO services(name,duration,price) VALUES (?,?,?)',(name,duration,price))
     elif action=='barber':
      name=request.form.get('name','').strip()
      if not name or len(name)>60:raise BookingError('Isi nama kapster, maksimal 60 karakter.')
      if request.form.get('id'):c.execute('UPDATE barbers SET name=?,active=? WHERE id=?',(name,1 if request.form.get('active') else 0,int(request.form['id'])))
      else:c.execute('INSERT INTO barbers(name) VALUES (?)',(name,))
     elif action=='hours':
      for day in range(7):
       op=request.form.get(f'open_{day}','');cl=request.form.get(f'close_{day}','');bs=request.form.get(f'bs_{day}','');be=request.form.get(f'be_{day}','');closed=1 if request.form.get(f'off_{day}') else 0
       times=[parse('2000-01-01T'+x) for x in [op,cl]]
       if times[0]>=times[1]:raise BookingError('Jam tutup harus sesudah jam buka; jadwal lintas tengah malam belum didukung.')
       if bool(bs)!=bool(be):raise BookingError('Isi kedua jam istirahat atau kosongkan keduanya.')
       if bs and not(times[0]<=parse('2000-01-01T'+bs)<parse('2000-01-01T'+be)<=times[1]):raise BookingError('Jam istirahat harus berada dalam jam kerja.')
       c.execute('UPDATE hours SET opened=?,closed=?,break_start=?,break_end=?,is_closed=? WHERE weekday=?',(op,cl,bs,be,closed,day))
     elif action=='block':
      start=parse(request.form.get('start'));end=parse(request.form.get('end'));bid=int(request.form.get('barber_id',0)) or None
      if end<=start:raise BookingError('Akhir blokir harus setelah waktu mulai.')
      reason=request.form.get('reason','').strip()[:100]
      if not reason:raise BookingError('Isi alasan blokir.')
      if c.execute("SELECT 1 FROM bookings WHERE (? IS NULL OR barber_id=?) AND status IN ('scheduled','arrived','serving') AND start<? AND end>?",(bid,bid,stamp(end),stamp(start))).fetchone():raise BookingError('Ada booking aktif pada interval itu. Hubungi pelanggan dan atur ulang booking sebelum memblokir.')
      c.execute('INSERT INTO blocks(barber_id,start,end,reason) VALUES (?,?,?,?)',(bid,stamp(start),stamp(end),reason))
     elif action=='unblock':c.execute('DELETE FROM blocks WHERE id=?',(int(request.form['id']),))
     else:raise BookingError('Tindakan tidak dikenal.')
    flash('Pengaturan disimpan. Booking lama tidak diubah otomatis; periksa kalender jika jam kerja berubah.','success')
   except (BookingError,ValueError) as e:flash(str(e),'error')
   return redirect(url_for('admin_settings'))
  with db() as c:services=c.execute('SELECT * FROM services').fetchall();barbers=c.execute('SELECT * FROM barbers').fetchall();hours=c.execute('SELECT * FROM hours ORDER BY weekday').fetchall();blocks=c.execute('SELECT x.*,k.name barber_name FROM blocks x LEFT JOIN barbers k ON k.id=x.barber_id ORDER BY start').fetchall()
  return render_template('settings.html',services=services,barbers=barbers,hours=hours,blocks=blocks)
 @app.route('/admin/messages',methods=['GET','POST'])
 @admin_only
 def messages():
  if request.method=='POST':
   try:n=process_messages(app.config['DATABASE'],app.config);flash(f'{n} pesan jatuh tempo diproses. Periksa status di bawah.','success')
   except Exception:flash('Pemrosesan tertunda; coba lagi setelah beberapa saat.','error')
   return redirect(url_for('messages'))
  with db() as c:rows=c.execute('SELECT m.*,b.code,b.name,b.phone FROM messages m JOIN bookings b ON b.id=m.booking_id ORDER BY m.id DESC LIMIT 100').fetchall()
  return render_template('messages.html',rows=rows)
 @app.route('/admin/analytics')
 @admin_only
 def analytics():
  with db() as c:data=insights(c);promos=c.execute('SELECT * FROM promos ORDER BY date DESC LIMIT 20').fetchall()
  return render_template('analytics.html',data=data,promos=promos)
 @app.route('/admin/promo',methods=['POST'])
 @admin_only
 def promo():
  try:
   with db() as c:
    if request.form.get('action')=='disable':c.execute('UPDATE promos SET active=0 WHERE id=?',(int(request.form['id']),))
    else:
     date=request.form.get('date','');start=request.form.get('start_time','');end=request.form.get('end_time','');label=request.form.get('label','').strip()
     if parse(date+'T'+start)<now() or parse(date+'T'+end)<=parse(date+'T'+start) or not 3<=len(label)<=100:raise BookingError('Isi tanggal, rentang jam mendatang, dan keterangan promo yang valid.')
     c.execute('INSERT INTO promos(date,start_time,end_time,label) VALUES (?,?,?,?)',(date,start,end,label))
   flash('Informasi promo diperbarui. Harga akhir tetap dicatat admin saat pembayaran.','success')
  except (BookingError,ValueError) as e:flash(str(e),'error')
  return redirect(url_for('analytics'))
 @app.errorhandler(404)
 def not_found(e):return render_template('error.html',title='Halaman tidak ditemukan',message='Periksa kembali tautan booking atau kembali ke beranda.'),404
 @app.errorhandler(400)
 def bad_request(e):return render_template('error.html',title='Permintaan belum dapat diproses',message=e.description),400
 return app

if __name__=='__main__':
 app=create_app()
 def worker():
  while True:
   try:process_messages(app.config['DATABASE'],app.config)
   except Exception:logging.exception('Outbox tertunda')
   time.sleep(15)
 threading.Thread(target=worker,daemon=True).start()
 print('\nRuang Pangkas: http://127.0.0.1:5000 | Buat akun admin: /setup\n')
 app.run(host='127.0.0.1',port=5000,debug=False,use_reloader=False)
