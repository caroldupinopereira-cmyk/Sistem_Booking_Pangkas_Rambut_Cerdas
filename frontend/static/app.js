'use strict';
document.querySelectorAll('.slot-form').forEach(form => {
    const list = form.querySelector('.slot-list'),
        start = form.querySelector('[name=start]'),
        submit = form.querySelector('.booking-submit'),
        date = form.querySelector('[name=date]');
    let revision = 0;
    const toDateString = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    if (date) {
        const max = new Date(date.min + 'T00:00:00');
        max.setDate(max.getDate() + 60);
        date.max = toDateString(max);
    }
    async function load() {
        const current = ++revision;
        start.value = '';
        submit.disabled = true;
        list.replaceChildren();
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.textContent = 'Memeriksa jadwal…';
        list.append(msg);
        const query = new URLSearchParams({
            date: date.value
        });
        if (form.dataset.token) query.set('token', form.dataset.token);
        else {
            const service = form.querySelector('[name=service_id]:checked'),
                barber = form.querySelector('[name=barber_id]');
            if (!service || !barber?.value) {
                msg.textContent = 'Layanan atau kapster belum tersedia.';
                return;
            }
            query.set('service', service.value);
            query.set('barber', barber.value);
        }
        try {
            const response = await fetch('/api/slots?' + query.toString(), {
                cache: 'no-store',
                signal: AbortSignal.timeout(25000),
                headers: {
                    Accept: 'application/json'
                }
            });
            if (!response.headers.get('content-type')?.includes('application/json')) {
                throw new Error('Jadwal gagal dimuat dari server. Muat ulang halaman atau coba lagi sebentar.');
            }
            const data = await response.json();
            if (current !== revision) return;
            list.replaceChildren();
            if (!response.ok) throw new Error(data.error || 'Jadwal belum dapat dimuat.');
            if (!data.slots.length) {
                msg.textContent = date.value === date.min
                    ? 'Tidak ada jam tersedia hari ini. Jika jam operasional sudah lewat atau penuh, pilih tanggal besok atau kapster lain.'
                    : 'Tidak ada jam tersedia pada tanggal ini. Coba tanggal atau kapster lain, atau hubungi admin untuk mengecek jam kerja.';
                list.append(msg);
                return;
            }
            for (const slot of data.slots) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'slot';
                button.textContent = slot.label;
                button.title = `${slot.label}–${slot.end} WIB`;
                button.setAttribute('aria-pressed', 'false');
                button.addEventListener('click', () => {
                    list.querySelectorAll('.slot').forEach(b => {
                        b.classList.remove('selected');
                        b.setAttribute('aria-pressed', 'false');
                    });
                    button.classList.add('selected');
                    button.setAttribute('aria-pressed', 'true');
                    start.value = slot.value;
                    submit.disabled = false;
                });
                list.append(button);
            }
        } catch (error) {
            if (current !== revision) return;
            list.replaceChildren();
            msg.textContent = error.name === 'TimeoutError'
                ? 'Memuat jadwal terlalu lama. Coba lagi atau pilih tanggal lain.'
                : (error.message || 'Koneksi gagal. Ubah tanggal untuk mencoba lagi.');
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn outline small';
            retry.textContent = 'Coba lagi';
            retry.addEventListener('click', load);
            list.append(retry);
            list.append(msg);
        }
    }
    form.querySelectorAll('[name=service_id],[name=barber_id],[name=date]').forEach(el => el.addEventListener('change', load));
    form.addEventListener('submit', event => {
        if (!start.value) {
            event.preventDefault();
            return;
        }
        submit.disabled = true;
        submit.textContent = 'Menyimpan…';
    });
    load();
});
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
document.querySelectorAll('[data-copy-link]').forEach(button => button.addEventListener('click', async () => {
    const result = document.querySelector('.copy-result');
    try {
        await navigator.clipboard.writeText(location.origin + location.pathname);
        result.textContent = 'Tautan berhasil disalin.';
    } catch {
        result.textContent = 'Salin alamat halaman ini dari kolom alamat browser.';
    }
}));

// Admin inbox polls only while visible; messages are rendered as text, never HTML.
(() => {
    const bell = document.querySelector('[data-admin-notifications]');
    if (!bell) return;
    const badge = bell.querySelector('[data-notification-count]');
    const list = document.querySelector('[data-notifications-list]');
    const read = document.querySelector('[data-notifications-read]');
    const status = document.querySelector('[data-notifications-status]');
    const pushStatus = document.querySelector('[data-push-status]');
    const announce = document.querySelector('[data-notification-announcement]');
    let latest = Number(list?.dataset.latestId || 0), previous = null, pending = false;
    async function post(url, data = {}) {
        const response = await fetch(url, {method:'POST',headers:{Accept:'application/json'},body:new URLSearchParams({csrf:bell.dataset.csrf,...data})});
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Sesi admin berakhir. Masuk kembali untuk melanjutkan.');
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Permintaan belum berhasil. Muat ulang halaman dan coba lagi.');
        return result;
    }
    async function refresh() {
        if (document.hidden || pending) return;
        pending = true;
        try {
            const response = await fetch('/admin/notifications',{cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(20000)});
            if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
            const data = await response.json();
            latest = data.latest_id;
            badge.textContent = data.unread > 99 ? '99+' : String(data.unread);
            badge.hidden = !data.unread;
            bell.setAttribute('aria-label',`${data.unread} notifikasi admin belum dibaca`);
            if (read) read.disabled = !data.unread;
            if (previous !== null && latest > previous && announce) announce.textContent = 'Ada pembaruan booking baru. Periksa daftar notifikasi.';
            previous = latest;
            if (list) {
                list.replaceChildren();
                for (const item of data.items) {
                    const link = document.createElement('a');
                    link.className = 'notification-item' + (item.read_at ? '' : ' is-unread');
                    link.href = '/booking/' + encodeURIComponent(item.token);
                    for (const [tag,text] of [['strong',item.title],['span',item.body],['small',item.created.replace('T',' ')+' WIB']]) {
                        const node = document.createElement(tag);node.textContent = text;link.append(node);
                    }
                    list.append(link);
                }
                if (!data.items.length) {const empty=document.createElement('p');empty.className='muted';empty.textContent='Belum ada notifikasi pelanggan.';list.append(empty);}
            }
            if (status) status.textContent = 'Terhubung. Pembaruan otomatis setiap 30 detik saat halaman aktif.';
        } catch {
            if (status) status.textContent = 'Pembaruan belum berhasil. Jika sesi berakhir, masuk kembali. Akan dicoba lagi otomatis.';
        } finally { pending = false; }
    }
    read?.addEventListener('click', async () => {
        read.disabled = true;
        try {await post('/admin/notifications/read',{through_id:latest});await refresh();}
        catch(error){if(status)status.textContent=error.message;read.disabled=false;}
    });
    const supported = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    document.querySelector('[data-push-enable]')?.addEventListener('click', async event => {
        const button=event.currentTarget;button.disabled=true;
        try {
            if (!supported) throw new Error('Browser belum mendukung push. Di iPhone/iPad, tambahkan ke Layar Utama dan buka aplikasi dari sana.');
            if (await Notification.requestPermission() !== 'granted') throw new Error('Izin belum diberikan. Atur izin notifikasi website pada browser jika ingin mengaktifkannya.');
            await navigator.serviceWorker.register('/sw.js');
            const registration=await navigator.serviceWorker.ready;
            const key=await post('/admin/notifications/push-key');
            const bytes=Uint8Array.from(atob(key.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-key.public_key.length%4)%4)),character=>character.charCodeAt(0));
            let subscription=await registration.pushManager.getSubscription();
            if(!subscription) subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});
            await post('/admin/notifications/subscribe',{subscription:JSON.stringify(subscription.toJSON())});
            pushStatus.textContent='Notifikasi perangkat aktif. Booking pelanggan berikutnya akan mengirim pemberitahuan ke perangkat ini.';
        } catch(error) {pushStatus.textContent=error.message;}
        finally {button.disabled=false;}
    });
    document.querySelector('[data-push-disable]')?.addEventListener('click',async event=>{
        const button=event.currentTarget;button.disabled=true;
        try {
            if(!supported)throw new Error('Push tidak tersedia pada browser ini.');
            const registration=await navigator.serviceWorker.getRegistration('/');
            const subscription=registration && await registration.pushManager.getSubscription();
            if(subscription){await post('/admin/notifications/unsubscribe',{endpoint:subscription.endpoint});await subscription.unsubscribe();}
            pushStatus.textContent='Notifikasi perangkat ini dinonaktifkan.';
        }catch(error){pushStatus.textContent=error.message;}
        finally{button.disabled=false;}
    });
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    setInterval(refresh,30000);
    refresh();
})();

(() => {
    const panel=document.querySelector('[data-customer-notifications]');
    if(!panel)return;
    let loading=false;
    async function refreshCustomerNotifications(){
        if(document.hidden||loading)return;loading=true;
        try{
            const response=await fetch('/dasbor/notifications',{cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(20000)});
            if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error();
            const data=await response.json(),list=panel.querySelector('[data-customer-notifications-list]');
            list.replaceChildren();
            for(const item of data.items){
                const link=document.createElement('a');link.className='notification-item is-unread';link.href='/booking/'+encodeURIComponent(item.token);
                for(const [tag,text] of [['strong',item.title],['span',item.body],['small',item.code+' · '+item.created.replace('T',' ')+' WIB']]){const node=document.createElement(tag);node.textContent=text;link.append(node);}
                list.append(link);
            }
            if(!data.items.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='Belum ada perubahan dari admin.';list.append(empty);}
            panel.querySelector('[data-customer-notifications-status]').textContent='Pembaruan diperiksa setiap 30 detik. Email operasional juga diantrekan untuk perubahan dan pembatalan booking online.';
        }catch{panel.querySelector('[data-customer-notifications-status]').textContent='Pembaruan otomatis belum berhasil. Muat ulang halaman untuk melihat perubahan terbaru.';}
        finally{loading=false;}
    }
    setInterval(refreshCustomerNotifications,30000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshCustomerNotifications();});
})();
