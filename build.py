"""Publish only approved frontend assets to Vercel CDN."""
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parent

def main():
    target = ROOT / "public" / "static"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js", "admin-icon.svg"):
        copyfile(ROOT / "static" / name, target / name)
    print("Prepared public frontend assets.")

if __name__ == "__main__":
    main()
