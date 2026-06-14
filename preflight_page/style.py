"""Paylaşılan renk/etiket sabitleri ve küçük widget yardımcıları.

Tüm parçalar buradaki tek kaynaktan beslenir ki renkler/etiketler (ve böylece
HTML raporuyla tutarlılık) tek yerde tanımlı kalsın.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout

# app/report.py ile birebir aynı renkler/etiketler
STATUS_COLOR = {"PASS": "#1d8348", "WARN": "#b7950b", "FAIL": "#c0392b",
                "INFO": "#2471a3", "SKIP": "#7f8c8d"}
STATUS_TR = {"PASS": "GEÇTİ", "WARN": "UYARI", "FAIL": "HATA",
             "INFO": "BİLGİ", "SKIP": "ATLANDI"}

# STATUSTEXT önem (severity) renkleri
SEV_COLOR = {
    0: "#7b241c", 1: "#922b21", 2: "#c0392b", 3: "#c0392b",
    4: "#b7950b", 5: "#2471a3", 6: "#566573", 7: "#7f8c8d",
}


def chip(text, color, parent=None):
    """Yuvarlatılmış, renkli rozet etiketi."""
    lbl = QLabel(text, parent)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(
        "background:%s;color:#fff;border-radius:8px;padding:1px 8px;"
        "font-weight:600;" % color)
    return lbl


def value_box(title):
    """Telemetri için 'başlık + büyük değer' kutusu. (frame, value_label) döner."""
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    frame.setStyleSheet(
        "QFrame{background:#f4f6f7;border:1px solid #d5d8dc;border-radius:8px;}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(8, 5, 8, 6)
    lay.setSpacing(1)
    t = QLabel(title)
    t.setStyleSheet("color:#7f8c8d;font-size:11px;border:none;background:none;")
    v = QLabel("—")
    v.setStyleSheet("color:#1c2833;font-size:15px;font-weight:700;"
                    "border:none;background:none;")
    v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(t)
    lay.addWidget(v)
    return frame, v


def verdict_style(verdict):
    """Karar bandı için (arkaplan rengi, metin) döndürür."""
    return STATUS_COLOR.get(verdict, "#7f8c8d")
