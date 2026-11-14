# Sistem Booking Pangkas Rambut Cerdas

Aplikasi lokal Python + Flask + SQLite, mengikuti alur Pelanggan -> Sistem -> Admin/Kapster. Nama tampilan awal **Ruang Pangkas** dapat diubah admin. Database pelanggan kosong; yang diisi otomatis hanya contoh layanan, kapster, dan jam kerja.

## Menjalankan di Visual Studio Code

1. Buka folder ini melalui **File > Open Folder**.
2. Buka **Terminal > New Terminal**.
3. Jalankan `python app.py`.
4. Buka http://127.0.0.1:5000 untuk pelanggan.
5. Pelanggan masuk/daftar di `/masuk` (Google atau email+password) lalu booking melalui `/dasbor`.
6. Buka http://127.0.0.1:5000/setup untuk membuat akun admin pertama (email + password), kemudian atur nama usaha, harga, kapster, jam kerja, dan toleransi.
7. Hentikan server dengan Ctrl+C. Database tetap tersimpan.

Bisa juga klik dua kali **JALANKAN.bat**. Python 3.10+ diperlukan. Flask sudah tersedia pada laptop yang dipakai saat pembuatan. Pada laptop baru, gunakan lingkungan virtual:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Tidak perlu MySQL atau XAMPP. Untuk F5, gunakan ekstensi Python dan Python Debugger yang direkomendasikan VS Code.

## Akun pelanggan & dasbor

- **Beranda (`/`)** hanya informasi (layanan, harga, kapster, promo, alamat) dengan tombol "Masuk atau daftar untuk booking"; tidak ada form booking publik.
- **Masuk (`/masuk`)**: tombol **Masuk dengan Google** (OAuth 2.0 authorization code, tanpa dependensi baru, pakai `urllib`) dan form email+password, serta tautan daftar.
- **Google OAuth**: dikonfigurasi via `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, default `http://127.0.0.1:5000/auth/google/callback`). Jika kredensial kosong, tombol Google disembunyikan dan aplikasi tetap jalan normal. Hanya email Google yang terverifikasi (`email_verified=true`) diterima. Jika email sudah ada, `google_sub` ditautkan dan `email_verified` diset `1`. Untuk mencegah pre-account-hijacking, akun email yang sebelumnya belum terverifikasi dan punya password akan **kehilangan password** saat ditautkan ke Google (masuk kembali lewat Google).
- **Daftar & verifikasi email**: daftar (nama, email, password minimal 10 karakter, ulangi password, persetujuan notifikasi). Kode verifikasi 6 digit dikirim ke email (berlaku 10 menit, sekali pakai, hash HMAC-SHA256 dengan `SECRET_KEY`, maksimal 5 salah lalu hangus, minta ulang minimal 60 detik, maksimal 5 kode/jam/email). Setelah benar, email terverifikasi dan otomatis login.
- **Login password**: pembatasan 8 percobaan/5 menit per sesi dan per email, pesan salah selalu "Email atau password tidak cocok." Akun belum terverifikasi bisa login tetapi booking diblokir dan dasbor menampilkan banner verifikasi + kirim ulang. Akun khusus Google yang mencoba login password mendapat pesan umum yang sama.
- **Lupa password**: belum disediakan (pengembangan lanjutan). Pelanggan yang akunnya tertaut Google dapat masuk dengan Google.
- **Dasbor (`/dasbor`, wajib login)**: ringkasan & booking terdekat, buat booking baru (form di dasbor, nama/email dari akun, hanya jika email terverifikasi), booking mendatang (ubah/batalkan, minimal 60 menit sebelum jadwal atau "Hubungi admin"), riwayat, profil (ubah nama, saklar persetujuan notifikasi, info metode masuk), keluar (`POST /keluar` + CSRF).
- **Keamanan kepemilikan**: pelanggan hanya bisa melihat/mengubah booking miliknya (kepemilikan diverifikasi); akses booking milik orang lain ditolak (404). Tautan `/booking/<token>` tetap berfungsi untuk link di email; jika dibuka pemilik, tersedia tombol kembali ke dasbor.
- Route `/book` lama dialihkan ke `/dasbor` atau `/masuk`.

## Alur booking & operasional

- Cek bentrok berdasarkan **seluruh interval** pada kapster yang sama. SQLite transaction mencegah dua pemesanan bersamaan pada slot yang sama.
- Bukti booking dan tautan acak pribadi `/booking/<token>` untuk melihat/mengelola booking.
- Reschedule dan pembatalan: minimal 60 menit sebelum jadwal aktif. Tepat 60 menit masih diterima. Pengecekan ulang ketika perubahan disimpan.
- Reschedule gagal tidak menghilangkan booking lama. Reschedule berhasil mengganti slot, mencatat histori, dan memperbarui antrean pengingat.
- Pembatalan melepas slot dan menghentikan pesan lama yang belum dikirim.
- Admin dapat membantu perubahan setelah tenggat atas kesepakatan pelanggan.
- Walk-in menggunakan kalender yang sama, kemudian admin mencatat hadir -> dilayani -> selesai beserta pembayaran.
- No-show membutuhkan toleransi yang disetujui admin. Booking dibatalkan tidak masuk no-show.
- Pengaturan layanan, kapster, jam kerja mingguan, blokir waktu, dan informasi promo.

## Pilihan implementasi versi awal

- Satu lokasi; zona waktu WIB (UTC+7); slot mulai setiap 15 menit; pemesanan maksimal 60 hari. Durasi layanan dapat berbeda dari grid slot.
- Jam kerja sama untuk semua kapster, tanpa jam kerja lintas tengah malam. Blokir dapat berlaku per kapster.
- Nilai awal layanan dan harga **contoh**, bukan hasil survei tempat cukur. Toleransi no-show dibiarkan kosong hingga ditentukan.
- Harga dan durasi disalin ke booking saat dibuat agar transaksi lama tidak berubah ketika tarif diperbarui.
- Perubahan jam kerja/penonaktifan kapster tidak mengubah booking lama otomatis. Admin perlu memeriksa booking terdampak. Blokir waktu yang menabrak booking aktif ditolak.
- Pembayaran di tempat dicatat manual. Informasi promo tidak menghitung potongan harga otomatis.
- Runtime pengingat berjalan di thread setiap 15 detik selama `python app.py` hidup. Menjalankan lewat server WSGI lain belum menyalakan worker; sediakan worker terpisah untuk deployment.
- Aplikasi untuk demo lokal; belum dideploy untuk publik. HTTPS, server produksi, backup terjadwal, dan operasi multi-lokasi belum disiapkan.

## Email (SMTP)

Default `MAIL_MODE=demo`: outbox disusun dan diproses, **tidak mengirim email**. Admin melihat status `Simulasi` di halaman Pesan Email. Email booking hanya dibuat jika ada alamat email dan persetujuan pemberitahuan.

**Kode verifikasi akun (sinkron, tidak lewat antrean 15 detik):**
- `MAIL_MODE=demo`: kode 6 digit dicetak ke terminal lewat logging `[DEMO] Kode verifikasi untuk x@y.com: 123456` dan disimpan sebagai pesan simulasi yang terlihat di halaman admin **Pesan Email**.
- `MAIL_MODE=smtp`: kode tidak pernah dicetak ke terminal/log. Jika pengiriman SMTP gagal, ditampilkan pesan umum tanpa detail server/password dan tanpa retry otomatis.

Booking lebih dari atau tepat dua jam sebelumnya mendapat pengingat dua jam sebelum jadwal. Booking kurang dari dua jam sebelumnya hanya konfirmasi. Reschedule mengikuti aturan yang sama terhadap jadwal baru. Pembatalan mengakhiri pengingat. Antrean diperiksa lagi terhadap versi jadwal sebelum dikirim.

### Mode SMTP (Gmail App Password, gratis)

1. Aktifkan verifikasi 2 langkah pada akun Google yang akan dipakai mengirim email.
2. Buka **Google Account > Keamanan > App Passwords** (Sandi Aplikasi), lalu buat App Password baru.
3. Salin `.env.example` menjadi `.env`, lalu isi:
   - `MAIL_MODE=smtp`
   - `SMTP_HOST=smtp.gmail.com`
   - `SMTP_PORT=587`
   - `SMTP_USER=` email Gmail pengirim
   - `SMTP_PASSWORD=` App Password (bukan password akun biasa)
   - `MAIL_FROM=` (opsional, default mengikuti `SMTP_USER`)
   - `PUBLIC_BASE_URL=` alamat aplikasi yang dapat diakses penerima
4. Restart aplikasi, lalu uji dengan email Anda sendiri.
5. Batas wajar Gmail gratis sekitar **500 email/hari** (dapat berubah sesuai kebijakan Google). Gunakan secukupnya untuk kebutuhan demo/operasional kecil.

Catatan: status pengiriman yang berhasil berarti email diterima server SMTP; keterlambatan/kegagalan akhir di sisi penerima tetap mungkin terjadi. Error jaringan/timeout dicatat sebagai status tidak pasti dan **tidak dicoba ulang otomatis** agar tidak terkirim ganda. **Jangan membagikan `.env`**, App Password, `secret.key`, atau database pelanggan.

## Tiga fitur sains data: baseline, bukan model terlatih

1. **Perkiraan jumlah hadir:** rata-rata hari sejenis setelah minimal 14 hari pencatatan penuh. Grafik aktual memasukkan online dan walk-in. Ini baseline; Random Forest/regresi dan pengukuran MAE belum diterapkan.
2. **Jam promo:** ranking jumlah kedatangan historis di hari/jam sejenis, kemudian filter jam masa depan yang masih memiliki slot. Perhitungan okupansi menit, uji pengaruh promo, dan simulasi keuntungan belum diterapkan.
3. **Risiko no-show:** frekuensi pribadi (berdasarkan email pelanggan) dengan smoothing 5 kasus terhadap frekuensi keseluruhan; tampil setelah ada setidaknya 20 booking online berlabel, 5 no-show dan 5 hadir. Ambang ini untuk prototipe, bukan bukti model layak. Belum ada Logistic Regression, Decision Tree, atau evaluasi precision/recall.

Tidak ada penolakan atau pembatalan otomatis berdasarkan skor. Data sintetis tidak dimasukkan ke database utama. Hari tanpa catatan dianggap nol pada baseline sehingga admin perlu mencatat kunjungan secara konsisten. Data tutup/libur historis dan evaluasi bias perlu ditambahkan sebelum mengambil keputusan operasional nyata.

## Struktur file

- `app.py`: halaman, formulir, login, CSRF, dan worker.
- `booking.py`: aturan slot, transaksi booking, reschedule, batal, status.
- `database.py`: skema SQLite dan konfigurasi awal.
- `messaging.py`: outbox, simulasi, dan pengiriman email SMTP.
- `analytics.py`: baseline analisis.
- `templates/`: tampilan HTML.
- `static/style.css`, `static/app.js`: desain responsif dan pemilihan slot.
- `tests/test_app.py`: pengujian aturan bisnis dan akses halaman.
- `instance/`: database dan secret lokal, tidak untuk diunggah publik.

## Pengujian

```powershell
python -m unittest discover -s tests -v
```

Tes memakai database sementara dan waktu terkontrol; tidak membuat pelanggan atau akun uji pada database utama, tidak mengirim email sungguhan.

## Penyimpanan database lokal

Folder proyek berada dalam jalur OneDrive. Jalankan satu salinan database pada satu komputer; jangan membuka database yang sama di dua mesin melalui sinkronisasi. Untuk salinan cadangan, hentikan server sebelum menyalin folder `instance`. Jangan membagikan `.env`, `secret.key`, atau database pelanggan.
