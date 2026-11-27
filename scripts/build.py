"""Publish only approved frontend assets to Vercel CDN."""
from shutil import copyfile
from backend.paths import ROOT, PUBLIC, FRONTEND, STATIC

def main():
    target = ROOT / PUBLIC / STATIC
    target.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js", "admin-icon.svg", "logo-barbershop.png"):
        copyfile(ROOT / FRONTEND / STATIC / name, target / name)
    print("Prepared public frontend assets.")

if __name__ == "__main__":
    main()
