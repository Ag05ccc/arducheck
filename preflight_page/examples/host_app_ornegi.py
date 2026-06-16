"""Örnek host PyQt5 uygulaması — preflight_page'i AÇILIR PENCERE olarak ekleme.

Bu dosya, senin kendi ana PyQt5 uygulamanı temsil eder: tek bir butonu vardır ve
o butona basınca ArduCheck uçuş öncesi kontrol penceresi (PreflightWindow) ayrı
bir pencere olarak açılır. Entegrasyonun tamamı `AnaPencere` sınıfındaki birkaç
satırdan ibarettir (bkz. ../ENTEGRASYON.md).

Çalıştırma:
    # Host kendi dronekit bağlantısını kurup pencereye verir (gerçek senaryo):
    python preflight_page/examples/host_app_ornegi.py --connect tcp:127.0.0.1:5760

    # Bağlantı vermezsen pencere kendi bağlantı çubuğunu gösterir:
    python preflight_page/examples/host_app_ornegi.py
"""

import argparse
import os
import sys

# --- Adım 1: preflight_page paketini import edilebilir kıl ------------------ #
# Bu dosya  <kök>/preflight_page/examples/host_app_ornegi.py  konumunda; üç üst
# klasör ArduCheck deposunun köküdür (preflight_page/ ve app/ orada).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from PyQt5.QtWidgets import (QApplication, QLabel, QMainWindow, QPushButton,
                             QVBoxLayout, QWidget)


class AnaPencere(QMainWindow):
    """Senin mevcut ana PyQt5 uygulamanı temsil eden örnek pencere."""

    def __init__(self, vehicle=None):
        super().__init__()
        self.vehicle = vehicle           # host'un dronekit bağlantısı (olabilir None)
        self._pf_win = None              # --- açılır pencere referansı (önemli!)
        self.setWindowTitle("Host Uygulama (örnek)")
        self.resize(440, 220)

        central = QWidget()
        lay = QVBoxLayout(central)
        durum = "bağlı" if vehicle is not None else "yok (pencere kendi bağlanır)"
        lay.addWidget(QLabel("Bu, senin ana PyQt uygulamanı temsil eder.\n"
                             "Araç bağlantısı: %s" % durum))
        btn = QPushButton("✈  Uçuş Öncesi Kontrol Penceresini Aç")
        btn.setStyleSheet("font-weight:700;padding:10px;")
        btn.clicked.connect(self.ucus_oncesi_kontrol_ac)
        lay.addWidget(btn)
        lay.addStretch(1)
        self.setCentralWidget(central)

    # --- Adım 3: butona bağlı açılır pencere --------------------------------- #
    def ucus_oncesi_kontrol_ac(self):
        from preflight_page import PreflightWindow
        # zaten açıksa yenisini açma, mevcut olanı öne getir (tek-örnek)
        if self._pf_win is not None and self._pf_win.isVisible():
            self._pf_win.raise_()
            self._pf_win.activateWindow()
            return
        # host'un Vehicle'ını ver; referansı sakla (yoksa pencere hemen kapanır)
        self._pf_win = PreflightWindow(vehicle=self.vehicle)
        self._pf_win.show()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="preflight_page açılır pencere entegrasyon örneği")
    parser.add_argument("--connect", default=None,
                        help="dronekit bağlantı dizesi (ör. tcp:127.0.0.1:5760). "
                             "Verilmezse pencere kendi bağlantı çubuğunu gösterir.")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args(argv)

    # Host uygulama TEK QApplication oluşturur (modül bunu paylaşır).
    app = QApplication(sys.argv)

    vehicle = None
    if args.connect:
        # --- Adım 2: host kendi dronekit bağlantısını kurar (gerçek uygulamada
        # bu bağlantı zaten vardır). Şim/connect yardımcısını kullanıyoruz.
        from preflight_page.dronekit_adapter import connect_vehicle
        print("Host: dronekit bağlanıyor ->", args.connect)
        vehicle = connect_vehicle(args.connect, baud=args.baud)
        print("Host: bağlandı.")

    win = AnaPencere(vehicle=vehicle)
    win.show()
    try:
        rc = app.exec_()
    finally:
        # --- Adım 5: bağlantının sahibi host'tur; host kapatır.
        if vehicle is not None:
            try:
                vehicle.close()
            except Exception:
                pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
