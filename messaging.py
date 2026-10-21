"""Outbox WhatsApp: simulasi default; Cloud API memerlukan template dan kredensial."""
import json,os,urllib.request,urllib.error
from database import connect
from booking import now,stamp,get_booking,parse

def process_messages(path,config):
 count=0
 # Lock serializes updates with cancellation/reschedule while a message is dispatched.
 with connect(path) as db:
  db.execute('BEGIN IMMEDIATE')
  rows=db.execute("SELECT * FROM messages WHERE status='pending' AND due<=? ORDER BY due LIMIT 10",(stamp(now()),)).fetchall()
  for m in rows:
   raw=db.execute('SELECT token FROM bookings WHERE id=?',(m['booking_id'],)).fetchone();b=get_booking(db,raw['token'])
   if b['version']!=m['version'] or (m['kind']=='reminder' and (b['status']!='scheduled' or parse(b['start'])<=now())):
    db.execute("UPDATE messages SET status='obsolete' WHERE id=?",(m['id'],));continue
   link=config.get('PUBLIC_BASE_URL','http://127.0.0.1:5000').rstrip('/')+'/booking/'+b['token']
   title={'confirmation':'Booking berhasil','reschedule':'Jadwal diperbarui','cancellation':'Booking dibatalkan','reminder':'Pengingat booking'}[m['kind']]
   when=parse(b['start']).strftime('%d/%m/%Y %H:%M')+' WIB'
   body=f"{title}. Halo {b['name']}, {b['service_name']} bersama {b['barber_name']} pada {when}. Kode {b['code']}. Kelola: {link}"
   status='simulated';error='';pid=None
   if config.get('WA_MODE','demo')=='cloud':
    required=['WA_TOKEN','WA_PHONE_NUMBER_ID','WA_API_VERSION','WA_TEMPLATE_'+m['kind'].upper()]
    if any(not config.get(k) for k in required) or not config.get('PUBLIC_BASE_URL','').startswith('https://'):
     status='failed';error='Konfigurasi Cloud API / alamat HTTPS belum lengkap.'
    else:
     payload={'messaging_product':'whatsapp','to':b['phone'],'type':'template','template':{'name':config['WA_TEMPLATE_'+m['kind'].upper()],'language':{'code':config.get('WA_LANGUAGE','id')},'components':[{'type':'body','parameters':[{'type':'text','text':v} for v in [b['name'],b['service_name'],b['barber_name'],when,b['code'],link]]}]}}
     url=f"https://graph.facebook.com/{config['WA_API_VERSION']}/{config['WA_PHONE_NUMBER_ID']}/messages"
     req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+config['WA_TOKEN'],'Content-Type':'application/json'},method='POST')
     try:
      with urllib.request.urlopen(req,timeout=8) as resp:result=json.load(resp)
      pid=result.get('messages',[{}])[0].get('id');status='accepted' if pid else 'unknown'
      if not pid:error='Respons tanpa ID pesan; periksa penyedia sebelum mengirim ulang.'
     except urllib.error.HTTPError as exc:status='failed';error=f'Cloud API menolak permintaan (HTTP {exc.code}). Periksa kredensial dan template.'
     except Exception:status='unknown';error='Status pengiriman tidak pasti. Periksa penyedia; tidak dicoba ulang otomatis.'
   db.execute('UPDATE messages SET status=?,preview=?,error=?,provider_id=?,processed_at=? WHERE id=?',(status,body,error,pid,stamp(now()),m['id']));count+=1
 return count
