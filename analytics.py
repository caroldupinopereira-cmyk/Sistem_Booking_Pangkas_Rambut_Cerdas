"""Baseline transparan. Tidak membuat data sintetis atau mengklaim model terlatih."""
from datetime import timedelta
from collections import Counter
from booking import now,parse,slots
from database import settings

def insights(db):
 at=now();today=at.date();cfg=settings(db)
 start=parse(cfg.get('tracking_started',at.isoformat())).date()
 completed_days=max(0,(today-start).days)
 attendance=db.execute("SELECT * FROM bookings WHERE arrived_at IS NOT NULL AND status!='cancelled'").fetchall()
 daily=Counter(parse(b['arrived_at']).date() for b in attendance)
 history=[{'date':(today-timedelta(days=i)).isoformat(),'count':daily[today-timedelta(days=i)]} for i in range(13,-1,-1)]
 ready=completed_days>=14;forecast=[]
 for i in range(1,8):
  target=today+timedelta(days=i);h=db.execute('SELECT * FROM hours WHERE weekday=?',(target.weekday(),)).fetchone()
  dates=[start+timedelta(days=j) for j in range(completed_days) if (start+timedelta(days=j)).weekday()==target.weekday()]
  predicted=round(sum(daily[d] for d in dates)/len(dates),1) if ready and dates else None
  forecast.append({'date':target.isoformat(),'value':0 if h and h['is_closed'] else predicted,'samples':len(dates),'closed':bool(h and h['is_closed'])})
 eligible=db.execute("SELECT * FROM bookings WHERE source='online' AND status IN ('completed','no_show')").fetchall()
 missed=sum(b['status']=='no_show' for b in eligible);risk_ready=len(eligible)>=20 and missed>=5 and len(eligible)-missed>=5
 risks=[]
 for b in db.execute("SELECT b.*,s.name service_name FROM bookings b JOIN services s ON s.id=b.service_id WHERE status='scheduled' AND source='online' ORDER BY start LIMIT 20"):
  # Baseline at prediction time: only resolved historical events.
  own=[h for h in eligible if h['phone']==b['phone'] and parse(h['start'])<at]
  score=round(100*(sum(h['status']=='no_show' for h in own)+5*missed/len(eligible))/(len(own)+5)) if risk_ready else None
  risks.append({'code':b['code'],'name':b['name'],'start':b['start'],'score':score,'samples':len(own)})
 promos=[]
 if ready:
  service=db.execute('SELECT * FROM services WHERE active=1 ORDER BY duration LIMIT 1').fetchone();barbers=db.execute('SELECT id FROM barbers WHERE active=1').fetchall()
  for f in forecast:
   if f['closed'] or not service:continue
   target=parse(f['date']);available=set()
   for barber in barbers:
    for slot in slots(db,service['id'],barber['id'],f['date']):available.add(parse(slot['value']).hour)
   dates=[start+timedelta(days=j) for j in range(completed_days) if (start+timedelta(days=j)).weekday()==target.weekday()]
   for hour in sorted(available):
    # Rank by arrivals in equivalent historical hours; not a causal promotion model.
    count=sum(parse(b['arrived_at']).date() in dates and parse(b['arrived_at']).hour==hour for b in attendance)
    promos.append({'date':f['date'],'hour':hour,'average':round(count/max(1,len(dates)),1),'samples':len(dates)})
  promos=sorted(promos,key=lambda x:(x['average'],x['date'],x['hour']))[:6]
 return dict(history=history,forecast=forecast,ready=ready,observed_days=completed_days,total_attended=len(attendance),no_show=missed,labeled=len(eligible),risk_ready=risk_ready,risks=risks,promos=promos,max_count=max([h['count'] for h in history]+[1]))
