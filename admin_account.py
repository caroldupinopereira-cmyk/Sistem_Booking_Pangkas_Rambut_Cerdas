"""Replace the sole admin from a trusted server terminal, never through a public route."""
import hmac
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
    app = create_app()
    try:
        code = getpass("Kode khusus penggantian admin: ")
        email = input("Email admin baru: ").strip()
        password = getpass("Password admin baru (minimal 10 karakter): ")
        if password != getpass("Ulangi password baru: "):
            raise ValueError("Konfirmasi password tidak cocok.")
        replace_admin(app.config["DATABASE"], app.config.get("ADMIN_CHANGE_CODE", ""), code, email, password)
    except (ValueError, BookingError) as error:
        print(str(error))
        return 1
    print("Akun admin berhasil diganti. Sesi admin lama tidak berlaku; masuk kembali dengan akun baru.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
