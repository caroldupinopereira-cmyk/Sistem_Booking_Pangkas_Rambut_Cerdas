"""Publish only the two explicitly approved frontend assets to Vercel CDN."""
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parent

def main():
    target = ROOT / "public" / "static"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js"):
        copyfile(ROOT / "static" / name, target / name)
    print("Prepared CSS and JavaScript for public/static.")

if __name__ == "__main__":
    main()
