"""Write a private import file for Vercel; never print its values."""
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent.parent

def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main():
    source = read_env(ROOT / ".env")
    target = ROOT / "work" / "vercel.env"
    previous = read_env(target)
    for name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        if not source.get(name):
            raise SystemExit("Konfigurasi Turso belum lengkap. Isi .env terlebih dahulu.")
    keys = ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            "MAIL_MODE", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
    values = {key: source[key] for key in keys if source.get(key)}
    for key in ("SECRET_KEY", "ADMIN_CHANGE_CODE", "CRON_SECRET"):
        value = source.get(key) or previous.get(key)
        values[key] = value if value and len(value) >= 32 else secrets.token_hex(32)
    # Do not import localhost URLs. The app derives its production domain from Vercel.
    target.parent.mkdir(exist_ok=True)
    target.write_text("".join(key + "=" + value + "\n" for key, value in values.items()), encoding="utf-8")
    print("File pribadi siap: work/vercel.env. Import ke Environment Variables Vercel.")
    print("Jangan unggah file ini ke GitHub atau kirim isinya ke chat.")

if __name__ == "__main__":
    main()
