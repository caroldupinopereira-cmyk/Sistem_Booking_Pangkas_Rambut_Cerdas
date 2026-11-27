self.addEventListener('push', event => {
    let data = {};
    try {data = event.data.json();} catch {}
    event.waitUntil(self.registration.showNotification(data.title || 'Ruang Pangkas', {
        body: data.body || 'Ada pembaruan booking. Buka dashboard admin.',
        tag: data.tag || 'booking-update',
        data: {url:'/admin#notifikasi'}
    }));
});
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil((async()=>{
        const windows=await clients.matchAll({type:'window',includeUncontrolled:true});
        for(const window of windows){if(new URL(window.url).origin===self.location.origin){await window.navigate('/admin#notifikasi');return window.focus();}}
        return clients.openWindow('/admin#notifikasi');
    })());
});
