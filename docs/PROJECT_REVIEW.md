# Pemeriksaan proyek — 15 November 2026

Pemeriksaan ini mencakup kode, struktur folder, entry point lokal/Vercel, aturan booking,
login, notifikasi, konfigurasi email, dan pengujian otomatis. Pemeriksaan tidak mengirim
email sungguhan, tidak mengganti admin, dan tidak memindahkan database pelanggan.
Perubahan pada pemeriksaan ini masih lokal; belum dipush atau dideploy.

## Yang sudah diperbaiki

| Temuan | Perbaikan |
|---|---|
| Kode inti dan alat administrasi bercampur di root | Kode dipindahkan ke ruang_pangkas/, alat bantu ke scripts/, panduan ke docs/. |
| SECRET_KEY kosong dari .env menimpa kunci lokal yang valid | Factory mempertahankan kunci hasil resolusi konfigurasi; regresinya diuji. |
| Respons Google tanpa token/sub atau email_verified bukan boolean true bisa lolos pemeriksaan awal | Validasi identitas diperketat sebelum akun dibuat atau sesi login diterbitkan. |
| Tombol proses email manual mengambil banyak kiriman pada satu request Vercel | Pada Vercel dibatasi satu pesan pelanggan dan satu pemberitahuan admin per request. |
| Pesan skema lama menyarankan menghapus database | Pesan diganti menjadi instruksi backup/migrasi. Tabel lama tidak dihapus diam-diam saat startup. |
| Import modul berpotensi bertabrakan dengan nama paket eksternal | Modul internal memakai relative import di package ruang_pangkas. |
| File pengujian sementara bercampur dengan ekspor konfigurasi privat | Pengujian berikutnya menggunakan work/tests/. Berkas lama di work/ tidak dihapus. |

## Yang masih perlu diprioritaskan

| Prioritas | Bagian | Tindakan berikutnya |
|---|---|---|
| Tinggi | Email produksi | Pastikan panel Admin > Pesan Email menampilkan konfigurasi lengkap dan uji kiriman nyata setelah SMTP_PASSWORD diperbaiki. Konfigurasi lokal tidak membuktikan konfigurasi Vercel sama. |
| Tinggi | Pengingat terjadwal | vercel.json belum memasang jadwal. Worker lokal tidak berjalan pada entry point Vercel; siapkan scheduler terautentikasi untuk /api/cron/messages, lalu uji waktu kirim. |
| Tinggi | Pembatasan percobaan login | Penghitungan percobaan admin/pelanggan masih disimpan dalam sesi. Tambahkan pembatasan persisten di server agar mengganti cookie tidak mereset batas percobaan. |
| Menengah | Backup dan pemulihan | Belum ada pekerjaan backup terjadwal dalam kode. Buat prosedur backup database online dan uji pemulihan sebelum penggunaan operasional rutin. |
| Menengah | Penanganan kiriman tidak pasti | Status unknown/dispatching memerlukan pemeriksaan penyedia; tidak boleh diulang sembarangan. Pertimbangkan worker antrian dan provider email dengan idempotency untuk skala lebih besar. |
| Menengah | Pemeliharaan route | web.py masih besar meskipun sudah terpisah dari layanan bisnis. Refactor berikutnya dapat memecah route menjadi Blueprint pelanggan, auth, dan admin. |
| Menengah | Analisis cerdas | analytics.py masih baseline statistik, belum model terlatih atau evaluasi MAE/precision/recall. Tetap gunakan label baseline sampai ada data dan evaluasi yang layak. |

## Hal yang sudah ada dan diverifikasi otomatis

- Transaksi booking dan pencegahan bentrok, reschedule, pembatalan, serta pencatatan pembayaran.
- Maksimal satu akun admin dan pembatalan sesi/perangkat lama setelah akun diganti.
- Email operasional online dipisahkan dari pilihan pengingat.
- Inbox admin dan pelanggan dibatasi berdasarkan sesi/kepemilikan.
- Klaim email/push persisten untuk menghindari pengiriman ganda.
- CSRF, escaping teks pelanggan, dan perlindungan cookie sesuai URL HTTPS.
- Asset build hanya menyalin berkas frontend yang diizinkan; rahasia dan database dikecualikan dari Git/upload.

## Hasil verifikasi

124 tes regresi lulus setelah pemindahan kode dan perbaikan. Enam pemeriksaan tambahan
(SQLite dan libSQL) lulus untuk kunci sesi kosong, validasi Google, dan batch email.
Pengiriman Gmail nyata, izin notifikasi OS, serta perilaku setelah deployment berikutnya
belum diuji oleh pemeriksaan lokal ini.

Jalankan dari root:

```powershell
python -m unittest discover -s tests -v
python app.py
python admin_account.py --help
python build.py
```

## Lokasi berkas

- [README utama](../README.md)
- [Panduan deployment](DEPLOYMENT.md)
- [Factory dan route](../ruang_pangkas/web.py)
- [Aturan booking](../ruang_pangkas/booking.py)
- [Database](../ruang_pangkas/database.py)
- [Email](../ruang_pangkas/messaging.py)
- [Notifikasi](../ruang_pangkas/notifications.py)
- [Analisis](../ruang_pangkas/analytics.py)

.env, instance/, dan work/ tetap privat. public/static/ adalah hasil build; ubah aset
sumber di static/, kemudian jalankan build.py. Entry point root yang tipis dipertahankan
agar perintah lama tetap dapat digunakan.
