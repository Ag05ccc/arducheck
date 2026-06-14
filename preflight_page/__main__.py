"""Bağımsız (tek-pencere) başlatıcı — modülü host olmadan test etmek için.

Örnekler:
    python -m preflight_page                         # bağlantıyı arayüzden kur
    python -m preflight_page --connect tcp:127.0.0.1:5760
    python -m preflight_page --connect /dev/ttyACM0 --baud 115200
    python -m preflight_page --connect udp:0.0.0.0:14550
"""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ArduCheck uçuş öncesi kontrol — bağımsız PyQt5 penceresi")
    parser.add_argument("--connect", default=None,
                        help="Bağlantı dizesi (ör. tcp:127.0.0.1:5760, "
                             "/dev/ttyACM0, udp:0.0.0.0:14550). Verilmezse "
                             "arayüzdeki bağlantı çubuğu kullanılır.")
    parser.add_argument("--baud", type=int, default=115200,
                        help="Seri bağlantı baud hızı (varsayılan 115200)")
    args = parser.parse_args(argv)

    from PyQt5.QtWidgets import QApplication
    from .page import PreflightWindow

    app = QApplication(sys.argv)
    win = PreflightWindow()
    win.show()
    if args.connect:
        # arayüz açıldıktan hemen sonra bağlan
        win.page.controller.connect_target(args.connect, args.baud)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
