# Persiapan deployment Ruang Pangkas

Status: koneksi Turso sudah diuji langsung dan tabel online sudah dibuat.
Konfigurasi Vercel, aset CDN, dan email transaksi tersedia. Deployment belum dilakukan.
Pengingat terjadwal memerlukan scheduler terpisah; belum diaktifkan.
Database lokal, .env, dan kredensial tidak dipindahkan otomatis.

## File hosting

- `ruang_pangkas/wsgi.py`: entry point Flask tanpa server lokal dan tanpa thread pengingat.
- `pyproject.toml`: Python 3.12 dan entry point Vercel `ruang_pangkas.wsgi:app`.
- `.vercelignore`: mengecualikan rahasia, database, backup, dan file lokal dari upload CLI.
- `.gitignore`: mengecualikan rahasia dan cadangan dari commit berikutnya.
- `scripts/build.py`: menyalin hanya CSS dan JavaScript ke public/static untuk CDN.
- `vercel.json`: durasi fungsi maksimal 60 detik.
- `scripts/prepare_vercel_env.py`: membuat work/vercel.env pribadi untuk import konfigurasi.

Tidak perlu konfigurasi `builds` atau `rewrites` lama di vercel.json.
Perilaku lokal tetap dijalankan lewat `python -m ruang_pangkas`.

## 1. Database permanen (langkah berikutnya)

Vercel Functions tidak menyediakan penyimpanan SQLite lokal yang persisten dan
bersama antar-instance. Jangan memindahkan database ke /tmp untuk data pelanggan.
Saat VERCEL=1, aplikasi menolak SQLite lokal. Isi TURSO_DATABASE_URL dan
TURSO_AUTH_TOKEN agar aplikasi memakai koneksi remote libSQL tanpa file lokal.
Token disimpan pada konfigurasi, tidak dimasukkan ke URL atau log.

Adapter menggunakan libsql==0.1.11. Jalankan `python -m pip install -r requirements.txt`.
Di .env, isi URL libsql://...turso.io dan token write database dari Turso.
Menjalankan aplikasi dengan URL terisi akan membuat tabel dan data contoh layanan,
kapster, dan jam kerja pada database online. Database lokal tidak diimpor otomatis.
Jika token kosong, aplikasi akan berhenti; tidak beralih diam-diam ke SQLite lokal.
Kosongkan TURSO_DATABASE_URL untuk kembali memakai SQLite lokal saat development.

Tes menggunakan driver libSQL asli pada database sementara lokal. Koneksi remote
dan inisialisasi tabel sudah berhasil; uji booking di domain deployment tetap diperlukan.

Buat database staging kosong terlebih dahulu. Migrasi data lokal adalah langkah
terpisah, dengan backup dan verifikasi jumlah baris serta relasi.

## 2. Environment Variables

Isi di Vercel Project Settings, bukan di repository:

| Variabel | Nilai |
|---|---|
| TURSO_DATABASE_URL | libsql://nama-database.turso.io |
| TURSO_AUTH_TOKEN | Token akses write database dari Turso |
| ADMIN_CHANGE_CODE | Kode pribadi untuk mengganti satu akun admin |
| SECRET_KEY | Nilai acak tetap, minimal 32 byte |
| PUBLIC_BASE_URL | Opsional; otomatis dari VERCEL_PROJECT_PRODUCTION_URL |
| GOOGLE_CLIENT_ID | Client ID web untuk domain deployment |
| GOOGLE_CLIENT_SECRET | Client Secret OAuth |
| GOOGLE_REDIRECT_URI | Opsional; otomatis dari domain produksi + /auth/google/callback |
| MAIL_MODE | smtp untuk kiriman asli, demo untuk simulasi |
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 |
| SMTP_USER | Email pengirim |
| SMTP_PASSWORD | Sandi aplikasi Gmail |
| MAIL_FROM | Email pengirim yang sama |
| CRON_SECRET | Kode scheduler acak minimal 32 karakter |

Buat secret secara pribadi dengan `python -c "import secrets; print(secrets.token_hex(32))"`.
Jangan mengganti SECRET_KEY setiap request atau setiap startup.
Cookie sesi otomatis menggunakan Secure ketika PUBLIC_BASE_URL memakai HTTPS.
Gunakan database dan rahasia terpisah untuk Preview dan Production.

Jalankan `python -m scripts.prepare_vercel_env`. Pada pengaturan Environment Variables
Vercel, import file `work/vercel.env` (atau salin isinya hanya ke formulir Vercel).
File ini berisi rahasia asli: jangan kirim ke chat atau commit ke GitHub.
PUBLIC_BASE_URL dan GOOGLE_REDIRECT_URI lokal sengaja tidak disertakan.
Pastikan pengaturan Automatically expose System Environment Variables aktif agar
VERCEL_PROJECT_PRODUCTION_URL tersedia. Jika tidak, isi kedua URL HTTPS secara manual.

## 3. Google login

Tambahkan URI callback HTTPS persis di Google Auth Platform > Clients.
Pertahankan client lokal untuk localhost; gunakan client terpisah untuk produksi.
Konfigurasikan Audience, pengguna pengujian, dan publikasi aplikasi Google sesuai
kebutuhan akun. Setelah domain tersedia, uji login langsung di domain tersebut.

## 4. Email dan pengingat

Saat Vercel menerima POST booking/perubahan/pembatalan yang berhasil, aplikasi
memproses maksimal satu email transaksi sebelum respons berakhir. Tidak memakai
thread yang bisa dihentikan setelah request. Backlog diperiksa melalui Pesan Email.
Klaim pesan di-commit sebelum SMTP sehingga transaksi database tidak dikunci selama
koneksi email. Klaim yang terputus tetap 'dispatching' untuk pemeriksaan manual;
tidak dicoba ulang otomatis. Pesan disusun dari jadwal saat klaim; perubahan booking
yang terjadi setelah klaim dapat bersamaan dengan email yang sudah dikirim ke SMTP.

Pengingat membutuhkan pemanggil terjadwal GET /api/cron/messages dengan header
Authorization: Bearer <CRON_SECRET>. Endpoint memproses satu pesan jatuh tempo per
panggilan dan menolak permintaan tanpa token yang cocok. Atur frekuensi sesuai beban.
Tidak ada cron berbayar atau akun scheduler yang dibuat otomatis. Vercel Hobby
membatasi cron harian; jangan mengandalkannya untuk pengingat dua jam sebelum layanan.
Pengujian pengingat otomatis tetap ditunda sesuai keputusan pengguna.

## 5. Urutan publikasi setelah integrasi selesai

1. Jalankan `python -m unittest discover -s tests -v` dan tes database remote.
2. Periksa file yang akan masuk Git: jangan sertakan .env, instance, atau backup.
3. Hubungkan repository ke Vercel, isi Environment Variables staging.
4. Deploy staging; uji login, booking bentrok, reschedule, pembatalan, dan email.
5. Buat admin pertama melalui /setup di lingkungan terlindungi sebelum membuka
   akses publik. /setup hanya menerima koneksi localhost: jalankan aplikasi lokal
   dengan konfigurasi Turso lalu buat admin melalui http://127.0.0.1:5000/setup.
   Setelah ada satu admin, /setup tidak membuat akun tambahan.
6. Siapkan backup database online dan lakukan uji produksi sebelum pelanggan nyata.

## Referensi

- https://vercel.com/docs/frameworks/backend/flask
- https://vercel.com/kb/guide/is-sqlite-supported-in-vercel

## Kinerja daftar jam dan startup

Daftar slot mengambil interval sibuk dengan lima query per tanggal/kapster.
Di Vercel, startup hanya memeriksa tabel yang sudah tersedia; pembuatan tabel dan
migrasi dijalankan terlebih dahulu melalui aplikasi lokal dengan konfigurasi Turso.
Jangan deploy ke database kosong sebelum inisialisasi lokal selesai.

## Notifikasi admin: migrasi dan uji perangkat

Versi notifikasi memerlukan tabel admin_notifications, push_subscriptions, dan
push_deliveries. Buat melalui init_db/create_app dari terminal lokal sebelum deploy.
Migrasi menambah tabel, tidak menghapus booking atau membuat ulang akun admin.
Inbox hanya mencatat peristiwa baru. Email ditujukan ke akun admin saat pengiriman.

Tidak diperlukan variabel VAPID baru: kunci push dibuat saat admin mengaktifkan
perangkat dan disimpan di settings database. Browser menerima kunci publik saja.
Jangan mengekspor settings database atau menampilkan nilai _push_private_key.
Kunci privat tidak dimasukkan ke halaman HTML atau respons JSON.

Setelah deploy: masuk admin, aktifkan perangkat dan izinkan notifikasi. Pada iOS,
tambahkan ke Layar Utama lalu buka dari ikon tersebut. Buat booking percobaan dari
akun pelanggan dan periksa inbox, email admin, serta pemberitahuan perangkat.
Uji push nyata memerlukan tindakan izin pengguna; tes otomatis memakai pengiriman
mock. Jangan mengklaim pengiriman OS sudah diuji hanya karena tes otomatis lulus.

## Lokasi kode setelah perapian

Kode aplikasi berada di ruang_pangkas/, HTML di templates/, dan aset di static/.
Entry point Vercel adalah ruang_pangkas.wsgi:app. Build menggunakan
python -m scripts.build. Jalankan lokal dengan python -m ruang_pangkas;
alat administrasi menggunakan python -m scripts.admin_account.
