# Sistem Booking Pangkas Rambut Cerdas

Aplikasi lokal Python + Flask + SQLite, mengikuti alur Pelanggan -> Sistem -> Admin/Kapster yang dibahas. Nama tampilan awal **Ruang Pangkas** dapat diubah admin. Database pelanggan kosong; yang diisi otomatis hanya contoh layanan, kapster, dan jam kerja.

## Menjalankan di Visual Studio Code

1. Buka folder ini melalui **File > Open Folder**.
2. Buka **Terminal > New Terminal**.
3. Jalankan `python app.py`.
4. Buka http://127.0.0.1:5000 untuk pelanggan.
5. Buka http://127.0.0.1:5000/setup untuk membuat akun admin pertama, kemudian atur nama usaha, harga, kapster, jam kerja, dan toleransi.
6. Hentikan server dengan Ctrl+C. Database tetap tersimpan.

Bisa juga klik dua kali **JALANKAN.bat**. Python 3.10+ diperlukan. Flask sudah tersedia pada laptop yang dipakai saat pembuatan. Pada laptop baru, gunakan lingkungan virtual:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Tidak perlu MySQL atau XAMPP. Untuk F5, gunakan ekstensi Python dan Python Debugger yang direkomendasikan VS Code.

## Alur yang sudah berjalan

- Pilih layanan/kapster/tanggal, lihat jam kosong, isi nama dan WA, lalu booking.
- Cek bentrok berdasarkan **seluruh interval** pada kapster yang sama. SQLite transaction mencegah dua pemesanan bersamaan pada slot yang sama.
- Bukti booking dan tautan acak pribadi untuk melihat/mengelola booking tanpa akun pelanggan.
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

## WhatsApp: simulasi dan integrasi

Default `WA_MODE=demo`: outbox disusun dan diproses, **tidak mengirim WA**. Admin melihat status `Simulasi` di halaman Pesan WhatsApp. Nama/nomor tidak dikirim keluar oleh mode demo. Pesan hanya dibuat jika ada nomor dan persetujuan pemberitahuan.

Booking lebih dari atau tepat dua jam sebelumnya mendapat pengingat dua jam sebelum jadwal. Booking kurang dari dua jam sebelumnya hanya konfirmasi. Reschedule mengikuti aturan yang sama terhadap jadwal baru. Pembatalan mengakhiri pengingat. Antrean diperiksa lagi terhadap versi jadwal sebelum dikirim.

Untuk pengiriman asli:

1. Siapkan akun WhatsApp Business Platform/Cloud API, nomor pengirim, token, dan versi API yang masih berlaku.
2. Siapkan empat template yang disetujui: konfirmasi, perubahan, pembatalan, pengingat. Adapter menggunakan enam parameter **body** berurutan: nama pelanggan, layanan, kapster, tanggal/jam, kode, tautan kelola booking. Masing-masing template perlu cocok dengan urutan ini.
3. Salin `.env.example` ke `.env`. Isi kredensial dan nama template, `WA_LANGUAGE`, serta `PUBLIC_BASE_URL` HTTPS yang dapat diakses pelanggan. Baru ubah `WA_MODE=cloud` dan restart.
4. Uji dengan nomor Anda sendiri sebelum digunakan untuk pelanggan.

Adapter memakai `POST https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/messages`. Referensi resmi: https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/messages/template/ (format template; kode aplikasi memakai HTTP Python langsung, bukan SDK tersebut).

Status `accepted` hanya berarti penyedia menerima pesan, bukan bukti delivered/read. Webhook delivery belum diterapkan. Error tidak diulang otomatis untuk menghindari kiriman ganda ketika status jaringan tidak pasti. Untuk mode Cloud diperlukan pengujian dengan akun nyata; belum diuji kirim sungguhan.

## Tiga fitur sains data: baseline, bukan model terlatih

1. **Perkiraan jumlah hadir:** rata-rata hari sejenis setelah minimal 14 hari pencatatan penuh. Grafik aktual memasukkan online dan walk-in. Ini baseline; Random Forest/regresi dan pengukuran MAE belum diterapkan.
2. **Jam promo:** ranking jumlah kedatangan historis di hari/jam sejenis, kemudian filter jam masa depan yang masih memiliki slot. Perhitungan okupansi menit, uji pengaruh promo, dan simulasi keuntungan belum diterapkan.
3. **Risiko no-show:** frekuensi pribadi dengan smoothing 5 kasus terhadap frekuensi keseluruhan; tampil setelah ada setidaknya 20 booking online berlabel, 5 no-show dan 5 hadir. Ambang ini untuk prototipe, bukan bukti model layak. Belum ada Logistic Regression, Decision Tree, atau evaluasi precision/recall.

Tidak ada penolakan atau pembatalan otomatis berdasarkan skor. Data sintetis tidak dimasukkan ke database utama. Hari tanpa catatan dianggap nol pada baseline sehingga admin perlu mencatat kunjungan secara konsisten. Data tutup/libur historis dan evaluasi bias perlu ditambahkan sebelum mengambil keputusan operasional nyata.

## Struktur file

- `app.py`: halaman, formulir, login, CSRF, dan worker.
- `booking.py`: aturan slot, transaksi booking, reschedule, batal, status.
- `database.py`: skema SQLite dan konfigurasi awal.
- `messaging.py`: outbox, simulasi, adapter Cloud API.
- `analytics.py`: baseline analisis.
- `templates/`: tampilan HTML.
- `static/style.css`, `static/app.js`: desain responsif dan pemilihan slot.
- `tests/test_app.py`: pengujian aturan bisnis dan akses halaman.
- `instance/`: database dan secret lokal, tidak untuk diunggah publik.

## Pengujian

```powershell
python -m unittest discover -s tests -v
```

Tes memakai database sementara dan waktu terkontrol; tidak membuat pelanggan atau akun uji pada database utama, tidak mengirim pesan sungguhan.

## Kerja kelompok melalui GitHub

Pemilik repository dapat mengundang anggota melalui **Settings > Collaborators > Add people**. Anggota perlu menerima undangan menggunakan akun GitHub masing-masing.

Setelah mendapat akses, setiap anggota menyalin repository ke laptop sendiri:

```powershell
git clone <URL-REPOSITORY>
cd Sistem_Booking_Pangkas_Rambut_Cerdas
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Ganti `<URL-REPOSITORY>` dengan URL repository kelompok. Sesuaikan perintah `cd` jika nama repository berbeda. Jalankan aplikasi mengikuti panduan di atas; setiap laptop membuat database dan akun admin lokal sendiri.

Untuk setiap tugas, ambil perubahan terbaru dan buat branch sendiri:

```powershell
git switch main
git pull --ff-only origin main
git switch -c fitur/nama-fitur
```

Setelah mengedit, periksa perubahan, jalankan pengujian, lalu kirim branch:

```powershell
python -m unittest discover -s tests -v
git diff
git add <file-yang-diubah>
git commit -m "Jelaskan perubahan"
git push -u origin fitur/nama-fitur
```

Buka Pull Request di GitHub menuju `main` agar teman bisa meninjau sebelum digabung. Gunakan nama branch yang berbeda untuk setiap tugas. File `.env`, `instance/`, database, dan secret lokal tidak dibagikan melalui Git.

## Penyimpanan database lokal

Folder proyek berada dalam jalur OneDrive. Jalankan satu salinan database pada satu komputer; jangan membuka database yang sama di dua mesin melalui sinkronisasi. Untuk salinan cadangan, hentikan server sebelum menyalin folder `instance`. Jangan membagikan `.env`, `secret.key`, atau database pelanggan.
