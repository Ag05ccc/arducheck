"""Manuel kontrol listesi — telemetriyle doğrulanamayan, operatörün gözle
onayladığı maddeler (CG, pitot kapağı, pervane, yüzey yönleri, failsafe testi…).
Tanım app/checklist_def.py'den gelir."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QGroupBox, QLabel, QScrollArea,
                             QVBoxLayout, QWidget)


class ManualChecklist(QWidget):
    itemToggled = pyqtSignal(str, bool)        # id, checked
    progressChanged = pyqtSignal(int, int)     # done, total

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._boxes = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        self.progress = QLabel("0 / 0 tamamlandı")
        self.progress.setStyleSheet("font-weight:700;")
        root.addWidget(self.progress)

        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        for section in items:
            box = QGroupBox(section["section"])
            bl = QVBoxLayout(box)
            for item in section["items"]:
                cb = QCheckBox(item["text"])
                cb.toggled.connect(
                    lambda checked, iid=item["id"]: self._toggled(iid, checked))
                self._boxes[item["id"]] = cb
                bl.addWidget(cb)
            hl.addWidget(box)
        hl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._total = len(self._boxes)
        self._update_progress()

    def _toggled(self, item_id, checked):
        self.itemToggled.emit(item_id, checked)
        self._update_progress()

    def _update_progress(self):
        done = sum(1 for cb in self._boxes.values() if cb.isChecked())
        self.progress.setText("%d / %d tamamlandı" % (done, self._total))
        self.progressChanged.emit(done, self._total)

    def done_count(self):
        return sum(1 for cb in self._boxes.values() if cb.isChecked())

    def total(self):
        return self._total
