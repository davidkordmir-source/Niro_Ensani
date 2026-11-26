"""Start the isolated Python runtime installed alongside the Windows launcher."""

from niro.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
