import unittest,tempfile,threading
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from database import connect,init_db
from booking import create_booking,change_booking,get_booking,slots,update_status,BookingError,stamp
from messaging import process_messages
from analytics import insights
from app import create_app

BASE=datetime(2026,10,9,8,0)
class BookingTests(unittest.TestCase):
 def setUp(self):
  self.test_root=Path(__file__).resolve().parents[1]/'work';self.test_root.mkdir(exist_ok=True)
  self.tmp=tempfile.TemporaryDirectory(dir=self.test_root);self.path=Path(self.tmp.name)/'test.sqlite3';init_db(self.path)
  self.clock=BASE;self.patches=[patch(m+'.now',side_effect=lambda:self.clock) for m in ['booking','messaging','analytics','app']]
  for p in self.patches:p.start()
  self.app=create_app({'TESTING':True,'DATABASE':str(self.path),'SECRET_KEY':'test-secret','WA_MODE':'demo'});self.client=self.app.test_client()
 def tearDown(self):
  for p in self.patches:p.stop()
  self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(self.test_root.resolve()))
  self.tmp.cleanup()
 def make(self,start='2026-10-10T10:00:00',barber=1,consent='1',source='online'):
  return create_booking(self.path,dict(name='Pelanggan Uji',phone='081234567890',service_id=1,barber_id=barber,start=start,wa_consent=consent),source=source)
 def row(self,token):
  with connect(self.path) as db:return dict(get_booking(db,token))
 def post(self,url,data):
  with self.client.session_transaction() as s:s['csrf']='test-csrf'
  return self.client.post(url,data={**data,'csrf':'test-csrf'})
 def login(self):
  with self.client.session_transaction() as s:s['admin']='test-admin'
 def test_duplicate_and_overlapping_slots(self):
  self.make()
  for date in ['2026-10-10T10:00:00','2026-10-10T10:15:00']:
   with self.assertRaises(BookingError):self.make(date)
  self.make('2026-10-10T10:30:00');self.make(barber=2)
 def test_concurrent_booking_only_one_succeeds(self):
  barrier=threading.Barrier(2)
  def run(_):
   barrier.wait()
   try:self.make();return True
   except BookingError:return False
  with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(run,range(2))),1)
 def test_break_closing_and_past(self):
  for start in ['2026-10-10T11:45:00','2026-10-10T19:45:00','2026-10-08T10:00:00']:
   with self.assertRaises(BookingError):self.make(start)
 def test_invalid_time_grid(self):
  with self.assertRaises(BookingError):self.make('2026-10-10T10:07:00')
 def test_blocks_and_off_day(self):
  with connect(self.path) as db:db.execute('INSERT INTO blocks(barber_id,start,end,reason) VALUES (1,?,?,?)',('2026-10-10T09:00:00','2026-10-10T11:00:00','Libur'))
  with self.assertRaises(BookingError):self.make()
  self.make(barber=2)
 def test_failed_reschedule_keeps_original(self):
  a=self.make();self.make('2026-10-10T11:00:00')
  with self.assertRaises(BookingError):change_booking(self.path,a,'reschedule','2026-10-10T11:00:00')
  self.assertEqual(self.row(a)['start'],'2026-10-10T10:00:00');self.assertEqual(self.row(a)['version'],1)
 def test_reschedule_and_old_reminder_invalidated(self):
  a=self.make();change_booking(self.path,a,'reschedule','2026-10-10T14:00:00')
  self.make('2026-10-10T10:00:00')
  with connect(self.path) as db:
   old=db.execute('SELECT status FROM messages WHERE booking_id=? AND version=1',(self.row(a)['id'],)).fetchall()
  self.assertTrue(all(r['status']=='obsolete' for r in old));self.assertEqual(self.row(a)['version'],2)
 def test_one_hour_boundary(self):
  a=self.make('2026-10-09T10:00:00');self.clock=datetime(2026,10,9,9,0)
  change_booking(self.path,a,'reschedule','2026-10-09T11:00:00');self.clock=datetime(2026,10,9,10,0,1)
  with self.assertRaises(BookingError):change_booking(self.path,a,'cancel')
 def test_admin_override_boundary(self):
  a=self.make('2026-10-09T10:00:00');self.clock=datetime(2026,10,9,9,30)
  change_booking(self.path,a,'cancel',actor='admin');self.assertEqual(self.row(a)['status'],'cancelled')
 def test_cancel_releases_slot_and_preserves_history(self):
  a=self.make();change_booking(self.path,a,'cancel');self.make()
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM history WHERE booking_id=?',(self.row(a)['id'],)).fetchone()[0],2)
 def test_no_reminder_under_two_hours(self):
  a=self.make('2026-10-09T09:00:00')
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE booking_id=?',(self.row(a)['id'],)).fetchone()[0],1)
 def test_demo_messages_idempotent(self):
  self.make();self.assertEqual(process_messages(self.path,{'WA_MODE':'demo'}),1);self.assertEqual(process_messages(self.path,{'WA_MODE':'demo'}),0)
  with connect(self.path) as db:self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE status='simulated'").fetchone()[0],1)
 def test_cancel_stops_reminder(self):
  a=self.make();change_booking(self.path,a,'cancel');self.clock=datetime(2026,10,10,8,0);process_messages(self.path,{'WA_MODE':'demo'})
  with connect(self.path) as db:self.assertEqual(db.execute("SELECT status FROM messages WHERE kind='reminder'").fetchone()[0],'obsolete')
 def test_consent_and_walkin(self):
  self.make(consent='');create_booking(self.path,dict(name='Walk In',phone='',service_id=1,barber_id=2,start='2026-10-10T10:00:00'),source='walkin')
  with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
 def test_status_sequence_and_payment_rollback(self):
  a=self.make('2026-10-09T10:00:00');self.clock=datetime(2026,10,9,10,0)
  with self.assertRaises(BookingError):update_status(self.path,a,'completed',35000)
  update_status(self.path,a,'arrived');update_status(self.path,a,'serving')
  with self.assertRaises(BookingError):update_status(self.path,a,'completed','bad')
  self.assertEqual(self.row(a)['status'],'serving');update_status(self.path,a,'completed','35000');self.assertEqual(self.row(a)['paid'],35000)
 def test_no_show_needs_tolerance_and_time(self):
  a=self.make('2026-10-09T10:00:00');self.clock=datetime(2026,10,9,10,1)
  with self.assertRaises(BookingError):update_status(self.path,a,'no_show')
  with connect(self.path) as db:db.execute("UPDATE settings SET value='15' WHERE key='tolerance'")
  with self.assertRaises(BookingError):update_status(self.path,a,'no_show')
  self.clock=datetime(2026,10,9,10,16);update_status(self.path,a,'no_show')
 def test_empty_analytics_is_not_fake(self):
  with connect(self.path) as db:d=insights(db)
  self.assertFalse(d['ready']);self.assertFalse(d['risk_ready']);self.assertEqual(d['total_attended'],0);self.assertTrue(all(f['value'] is None for f in d['forecast']))
 def test_csrf_and_admin_access(self):
  self.assertEqual(self.client.post('/book',data={}).status_code,400);self.assertEqual(self.client.get('/admin').status_code,302)
 def test_all_pages_render(self):
  self.assertEqual(self.client.get('/').status_code,200);self.assertEqual(self.client.get('/setup').status_code,200)
  a=self.make();self.assertEqual(self.client.get('/booking/'+a).status_code,200);self.login()
  for url in ['/admin','/admin/settings','/admin/walkin','/admin/messages','/admin/analytics','/booking/'+a]:
   with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
 def test_setup_password_and_login(self):
  self.post('/setup',dict(username='pemilik',password='contoh-password-ku'))
  self.post('/logout',{})
  self.assertEqual(self.post('/login',dict(username='pemilik',password='salah')).status_code,200)
  self.assertEqual(self.post('/login',dict(username='pemilik',password='contoh-password-ku')).status_code,302)
 def test_api_slots_and_unknown_booking(self):
  data=self.client.get('/api/slots?service=1&barber=1&date=2026-10-10').get_json();self.assertTrue(data['slots'])
  self.assertEqual(self.client.get('/api/slots?service=1&barber=1&date=bad').status_code,400)
  self.assertEqual(self.client.get('/booking/not-a-token').status_code,404)
 def test_customer_name_is_escaped(self):
  a=create_booking(self.path,dict(name='<script>alert(1)</script>',phone='081234567890',service_id=1,barber_id=1,start='2026-10-10T10:00:00'))
  body=self.client.get('/booking/'+a).get_data(as_text=True);self.assertNotIn('<script>alert(1)</script>',body);self.assertIn('&lt;script&gt;',body)

if __name__=='__main__':unittest.main()
