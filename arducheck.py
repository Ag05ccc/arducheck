#!/usr/bin/env python3
"""ArduCheck — ArduPilot uçuş öncesi kontrol uygulaması.

Kullanım:
    python3 arducheck.py             # sunucuyu başlatır ve tarayıcıyı açar
    python3 arducheck.py --port 9000 # farklı HTTP portu
    python3 arducheck.py --no-browser
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows konsolu (cp1254/cp437) Türkçe karakterlerde çökebilir; UTF-8'e geç.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main():
    try:
        import pymavlink  # noqa: F401
        import serial  # noqa: F401  (pyserial — gerekli, seri port bağlantısı)
    except ImportError as exc:
        print("HATA: gerekli paket eksik (%s).\n"
              "Kurmak için:  pip install pymavlink pyserial\n"
              "veya kurulum betiğini çalıştırın (kur_ubuntu.sh / kur_windows.bat)"
              % exc.name)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="ArduPilot uçuş öncesi kontrol")
    parser.add_argument("--port", type=int, default=8642,
                        help="Web arayüzü portu (varsayılan 8642)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Tarayıcıyı otomatik açma")
    args = parser.parse_args()

    from server import serve
    serve(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
