"""Parametre referansı — mevcut parametreleri referans kaydet, .param dosyası
yükle, araç ile referans arasındaki farkları listele. Motor mantığı controller'da
(app/checks.py'deki yardımcılar)."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)


class ParamPanel(QWidget):
    def __init__(self, controller, notify, parent=None):
        super().__init__(parent)
        self._c = controller
        self._notify = notify
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        note = QLabel("Referans dosyası (ref_params.param) ile aracın mevcut "
                      "parametreleri karşılaştırılır. Farklar kontrol koşusunda "
                      "“Parametre Referansı” grubunda da görünür.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        root.addWidget(note)

        self.info = QLabel("—")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        box = QGroupBox("İşlemler")
        bl = QHBoxLayout(box)
        save = QPushButton("Mevcutu Referans Kaydet")
        save.clicked.connect(self._save)
        upload = QPushButton("Dosya Yükle…")
        upload.clicked.connect(self._upload)
        diff = QPushButton("Farkları Göster")
        diff.clicked.connect(self._diff)
        bl.addWidget(save)
        bl.addWidget(upload)
        bl.addWidget(diff)
        bl.addStretch(1)
        root.addWidget(box)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Parametre", "Referans", "Araç"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.table, 1)

        self.refresh_info()

    def refresh_info(self):
        info = self._c.refparams_info()
        if not info.get("exists"):
            self.info.setText("Referans dosyası yok — “Mevcutu Referans Kaydet” "
                              "ile oluşturabilir ya da bir .param dosyası "
                              "yükleyebilirsiniz.")
        else:
            cnt = info.get("count", "?")
            self.info.setText("Referans: %s (%s parametre)"
                              % (info.get("path"), cnt))

    def _save(self):
        ok, msg = self._c.save_ref_current()
        self._notify("info" if ok else "warn", msg)
        self.refresh_info()

    def _upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Referans .param dosyası seç", "",
            "Parametre dosyaları (*.param *.parm *.txt);;Tüm dosyalar (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            self._notify("error", "Dosya okunamadı: %s" % exc)
            return
        ok, msg = self._c.upload_ref(text)
        self._notify("info" if ok else "warn", msg)
        self.refresh_info()

    def _diff(self):
        payload, err = self._c.param_diff()
        if err:
            self._notify("warn", err)
            self.summary.setText(err)
            self.table.setRowCount(0)
            return
        diffs = payload["diffs"]
        missing = payload["missing"]
        bits = ["%d parametre karşılaştırıldı" % payload["ref_count"],
                "%d fark" % len(diffs)]
        if payload["ignored_volatile"]:
            bits.append("%d uçucu yoksayıldı" % payload["ignored_volatile"])
        if missing:
            bits.append("%d araçta yok" % len(missing))
        if not payload["param_fetch_done"]:
            bits.append("UYARI: araç parametreleri tam indirilmedi")
        self.summary.setText(" · ".join(bits))

        self.table.setRowCount(len(diffs))
        for i, d in enumerate(diffs):
            self.table.setItem(i, 0, QTableWidgetItem(d["name"]))
            ref = QTableWidgetItem("%.6g" % d["ref"])
            cur = QTableWidgetItem("%.6g" % d["cur"])
            ref.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cur.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 1, ref)
            self.table.setItem(i, 2, cur)
        if not diffs:
            self.summary.setText(self.summary.text()
                                 + "  —  araç referansla uyumlu. ✔")
