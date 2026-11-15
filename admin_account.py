"""Compatibility entry point; implementation lives in scripts/."""
from scripts.admin_account import main, replace_admin

if __name__ == "__main__":
    raise SystemExit(main())
