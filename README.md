# Gmail Checker Business — Vercel + Supabase + Midtrans

Bot Telegram berbasis webhook (bukan polling) untuk deployment di Vercel.

## Fitur starter
- Telegram webhook untuk menerima update.
- Supabase PostgreSQL: pengguna, wallet, transaksi, VIP, ban, dan riwayat.
- Midtrans Snap Sandbox untuk top-up.
- Verifikasi signature notifikasi Midtrans dan pemrosesan top-up idempotent di database.
- 5 slot API Emailvalidation.io, satu slot aktif dipilih melalui `EV_ACTIVE_KEY`.
- Pemeriksaan batch maksimal 50 alamat Gmail per pesan.
- Wallet debit atomik; pengecekan berbayar sesuai `PRICE_PER_EMAIL`.
- VIP 30 hari via `/vip`; pengecekan tidak dikenakan biaya per alamat selama VIP aktif.

**Penting:** API provider hanya memberikan sinyal deliverability. API ini tidak membuktikan bahwa akun Gmail terdaftar, aktif, atau pernah login. Gunakan hanya untuk kebutuhan yang sah dan sesuai ketentuan provider.

## 1. Supabase
1. Buat project di https://supabase.com.
2. Buka SQL Editor.
3. Jalankan seluruh isi `supabase/schema.sql`.
4. Dari Project Settings → API, salin Project URL dan `service_role` key.
5. Jangan pernah meletakkan service-role key di frontend atau mengirimkannya ke orang lain.

## 2. Midtrans Sandbox
1. Buat akun merchant di https://dashboard.sandbox.midtrans.com.
2. Ambil Server Key dari Settings → Access Keys.
3. Tetapkan `MIDTRANS_IS_PRODUCTION=false`.
4. Setelah deploy, masukkan URL notifikasi:
   `https://YOUR-VERCEL-DOMAIN/api/midtrans`
5. Uji pembayaran Sandbox terlebih dahulu. Jangan aktifkan Production sebelum menguji notifikasi, nominal, duplikasi, dan status transaksi.

## 3. Telegram
1. Buat bot lewat BotFather.
2. Simpan token sebagai `TELEGRAM_TOKEN`.
3. Dapatkan Telegram numeric user ID dan isi `BOT_OWNER_IDS`.
4. Buat nilai acak panjang untuk `TELEGRAM_WEBHOOK_SECRET`.

## 4. Deploy ke Vercel
1. Upload isi folder proyek ini ke repository GitHub.
2. Import repository di https://vercel.com.
3. Tambahkan semua variabel dari `.env.example` di Project → Settings → Environment Variables.
4. Deploy.
5. Buka `https://YOUR-VERCEL-DOMAIN/` untuk health check.
6. Daftarkan webhook Telegram dengan mengirim POST JSON ke endpoint admin:
   `https://YOUR-VERCEL-DOMAIN/api/admin/telegram-webhook`
   Header: `X-Admin-Secret: nilai-ADMIN_WEBHOOK_SECRET`.
   Body: `{"url":"https://YOUR-VERCEL-DOMAIN"}`. Endpoint ini dilindungi secret admin.

## 5. Perintah bot
- `/start`
- `/help`
- `/id`
- `/saldo`
- `/topup 10000`
- `/vip`
- `/vipinfo`
- `/cek`
- `/history`

Kirim daftar alamat Gmail untuk mulai mengecek. Batas 50 alamat per pesan.

## 6. Environment variables
Lihat `.env.example`. `BOT_OWNER_IDS` menerima satu atau lebih numeric Telegram ID, dipisahkan koma.
`EV_ACTIVE_KEY` memilih slot API 1–5. Slot tidak dirotasi otomatis untuk menghindari batas kuota provider.

## Catatan sebelum produksi
- Ini adalah starter project, bukan layanan yang telah diuji dengan kredensial akun Anda. Uji seluruh alur di Sandbox.
- Vercel serverless memiliki batas durasi eksekusi. Proses batch yang lama dapat melampaui batas fungsi; untuk volume tinggi, pindahkan pekerjaan batch ke worker/queue pada layanan worker yang selalu aktif.
- Tambahkan autentikasi admin khusus untuk endpoint pendaftaran webhook sebelum penggunaan publik.
- Atur harga, kebijakan refund, batas pemakaian, dan ketentuan layanan sebelum menerima pembayaran nyata.
- Pertimbangkan persetujuan privasi dan retensi data; jangan menyimpan daftar email lebih lama dari yang diperlukan.
