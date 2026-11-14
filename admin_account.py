"""Replace the sole admin from a trusted server terminal, never through a public route."""
import hmac
import argparse
from getpass import getpass
from app import create_app
from booking import normalize_email, BookingError
from database import connect
from werkzeug.security import generate_password_hash


def replace_admin(path, configured_code, supplied_code, email, password):
    if len(str(configured_code or "")) < 32:
        raise ValueError("ADMIN_CHANGE_CODE harus diatur dengan minimal 32 karakter.")
    if not hmac.compare_digest(str(configured_code), str(supplied_code)):
        raise ValueError("Kode khusus tidak cocok. Akun tidak diubah.")
    email = normalize_email(email)
    if len(password) < 10:
        raise ValueError("Password minimal 10 karakter.")
    password_hash = generate_password_hash(password)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        accounts = db.execute("SELECT id FROM admins").fetchall()
        if len(accounts) != 1:
            raise ValueError("Harus ada tepat satu akun admin. Gunakan /setup untuk akun pertama.")
        db.execute("UPDATE admins SET email=?, password=? WHERE id=?", (email, password_hash, accounts[0]["id"]))


def main():
    parser = argparse.ArgumentParser(description="Ganti satu akun admin menggunakan kode khusus.")
    parser.add_argument("--paste-password", action="store_true", help="Tampilkan input password agar mendukung paste terminal.")
    args = parser.parse_args()
    app = create_app()
    try:
        print("Kode khusus akan terlihat saat ditempel. Jangan bagikan tangkapan layar terminal ini.")
        code = input("Kode khusus penggantian admin: ").strip()
        email = input("Email admin baru: ").strip()
        password_input = getpass
        if args.paste_password:
            print("Mode paste aktif: password akan terlihat. Gunakan di layar pribadi.")
            password_input = input
        password = password_input("Password admin baru (minimal 10 karakter): ")
        if args.paste_password:
            if input("Periksa password di atas. Ketik SIMPAN untuk mengganti admin: ").strip().upper() != "SIMPAN":
                raise ValueError("Dibatalkan. Akun admin tidak diubah.")
        elif password != password_input("Ulangi password baru: "):
            raise ValueError("Konfirmasi password tidak cocok.")
        replace_admin(app.config["DATABASE"], app.config.get("ADMIN_CHANGE_CODE", ""), code, email, password)
    except (ValueError, BookingError) as error:
        print(str(error))
        return 1
    print("Akun admin berhasil diganti. Sesi admin lama tidak berlaku; masuk kembali dengan akun baru.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
