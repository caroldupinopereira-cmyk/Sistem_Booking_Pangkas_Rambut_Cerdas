'use strict';
document.querySelectorAll('.slot-form').forEach(form=>{
 const list=form.querySelector('.slot-list'),start=form.querySelector('[name=start]'),submit=form.querySelector('.booking-submit'),date=form.querySelector('[name=date]');let revision=0;
 const toDateString=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
 if(date){const max=new Date(date.min+'T00:00:00');max.setDate(max.getDate()+60);date.max=toDateString(max);}
 async function load(){
  const current=++revision;start.value='';submit.disabled=true;list.replaceChildren();const msg=document.createElement('p');msg.className='muted';msg.textContent='Memeriksa jadwal…';list.append(msg);
  const query=new URLSearchParams({date:date.value});
  if(form.dataset.token)query.set('token',form.dataset.token);
  else{const service=form.querySelector('[name=service_id]:checked'),barber=form.querySelector('[name=barber_id]');if(!service||!barber?.value){msg.textContent='Layanan atau kapster belum tersedia.';return;}query.set('service',service.value);query.set('barber',barber.value);}
  try{
   const response=await fetch('/api/slots?'+query.toString(),{headers:{Accept:'application/json'}});const data=await response.json();if(current!==revision)return;list.replaceChildren();if(!response.ok)throw new Error(data.error||'Jadwal belum dapat dimuat.');
   if(!data.slots.length){msg.textContent='Belum ada slot pada pilihan ini. Coba tanggal atau kapster lain.';list.append(msg);return;}
   for(const slot of data.slots){const button=document.createElement('button');button.type='button';button.className='slot';button.textContent=slot.label;button.title=`${slot.label}–${slot.end} WIB`;button.setAttribute('aria-pressed','false');button.addEventListener('click',()=>{list.querySelectorAll('.slot').forEach(b=>{b.classList.remove('selected');b.setAttribute('aria-pressed','false');});button.classList.add('selected');button.setAttribute('aria-pressed','true');start.value=slot.value;submit.disabled=false;});list.append(button);}
  }catch(error){if(current!==revision)return;list.replaceChildren();msg.textContent=error.message||'Koneksi gagal. Ubah tanggal untuk mencoba lagi.';list.append(msg);}
 }
 form.querySelectorAll('[name=service_id],[name=barber_id],[name=date]').forEach(el=>el.addEventListener('change',load));form.addEventListener('submit',event=>{if(!start.value){event.preventDefault();return;}submit.disabled=true;submit.textContent='Menyimpan…';});load();
});
document.querySelectorAll('form[data-confirm]').forEach(form=>form.addEventListener('submit',event=>{if(!window.confirm(form.dataset.confirm))event.preventDefault();}));
document.querySelectorAll('[data-copy-link]').forEach(button=>button.addEventListener('click',async()=>{const result=document.querySelector('.copy-result');try{await navigator.clipboard.writeText(location.origin+location.pathname);result.textContent='Tautan berhasil disalin.';}catch{result.textContent='Salin alamat halaman ini dari kolom alamat browser.';}}));
