# preflight_page'i Başka Bir PyQt5 Uygulamasına Açılır Pencere Olarak Ekleme

Bu kılavuz, `preflight_page` modülünü **mevcut bir ana PyQt5 uygulamasına** (host)
bir **butonla açılan yeni pencere** olarak nasıl ekleyeceğini adım adım anlatır.
Tam çalışan örnek: [`examples/host_app_ornegi.py`](examples/host_app_ornegi.py).

---

## Özet (TL;DR)

```python
from preflight_page import PreflightWindow

def ucus_oncesi_kontrol_ac(self):
    # zaten açıksa yenisini açma, öne getir
    if getattr(self, "_pf_win", None) is not None and self._pf_win.isVisible():
        self._pf_win.raise_(); self._pf_win.activateWindow(); return
    self._pf_win = PreflightWindow(vehicle=self.vehicle)  # mevcut dronekit Vehicle'ın
    self._pf_win.show()
```

İki kural yeterli:
1. `PreflightWindow`'a host uygulamanın **dronekit `Vehicle`**'ını ver.
2. Dönen pencereyi **bir örnek değişkende sakla** (`self._pf_win`), yoksa Python
   onu çöpe atar ve pencere anında kapanır.

---

## Önkoşullar

Host uygulamanın çalıştığı Python ortamına bağımlılıkları kur:

```bash
pip install PyQt5 dronekit future
# (future, Python 3.10+ için dronekit'in 'past' modülü için gereklidir)
```

> Python 3.10+ uyumluluk şimi (`collections.MutableMapping`) modül tarafından
> dronekit import edilmeden önce **otomatik** uygulanır; senin bir şey yapmana
> gerek yok. `future` paketinin kurulu olması yeterli.

---

## Adım 1 — Paketi import edilebilir kıl

Host uygulaman farklı bir klasörde olabilir. `preflight_page` paketinin
bulunabilmesi için **ArduCheck deposunun kök klasörünü** (yani `preflight_page/`
ve `app/` klasörlerinin bulunduğu dizini) Python yoluna ekle.

Üç yoldan biri:

**a) Çalışma zamanında `sys.path`'e ekle** (en pratik, host koduna 2 satır):
```python
import sys
sys.path.insert(0, "/home/gz/arducheck")   # preflight_page/ ve app/ bunun altında
from preflight_page import PreflightWindow
```

**b) Ortam değişkeniyle:**
```bash
export PYTHONPATH=/home/gz/arducheck:$PYTHONPATH
```

**c) Depoyu host projenin yanına kopyala/symlink'le** ve normal import et.

> Not: `app/` paketi (ortak core + kontrol motorları) deponun kökünde
> `preflight_page/`'in yanında durmalıdır — modül onu oradan import eder ve kökü
> otomatik `sys.path`'e ekler. Yani sadece **kökü** yola eklemen yeterli.

---

## Adım 2 — dronekit `Vehicle`'ını hazırla

Host uygulamanda araç bağlantısı zaten dronekit ile kuruluysa, o `Vehicle`
nesnesini doğrudan kullanacaksın. Henüz yoksa şöyle kurulur:

```python
from dronekit import connect
vehicle = connect("tcp:127.0.0.1:5760", wait_ready=True, baud=115200)
# gerçek araç: "/dev/ttyACM0" (USB, 115200) · "/dev/ttyUSB0" (telemetri, 57600)
#              "udp:0.0.0.0:14550" (GCS yayını)
```

> Kolaylık olsun diye modül bir yardımcı da sunar (3.10+ şimini içerir):
> ```python
> from preflight_page.dronekit_adapter import connect_vehicle
> vehicle = connect_vehicle("tcp:127.0.0.1:5760", baud=115200)
> ```

**Bağlantının sahibi kimdir?** Host `Vehicle`'ı verdiğinde, pencere bağlantının
**sahibi değildir**: pencere kapanınca yalnızca kendi MAVLink dinleyicisini
kaldırır, `Vehicle`'ı **kapatmaz**. `Vehicle`'ı host uygulaman açtı, host
uygulaman kapatır (`vehicle.close()`).

---

## Adım 3 — Açılır pencereyi aç

Ana penceredeki bir butona bağla. **Tek-örnek koruması** ve **referans saklama**
ile:

```python
class AnaPencere(QMainWindow):
    def __init__(self, vehicle):
        super().__init__()
        self.vehicle = vehicle
        self._pf_win = None                      # pencere referansı
        btn = QPushButton("✈ Uçuş Öncesi Kontrol", self)
        btn.clicked.connect(self.ucus_oncesi_kontrol_ac)
        # ... butonu kendi düzenine ekle ...

    def ucus_oncesi_kontrol_ac(self):
        from preflight_page import PreflightWindow
        if self._pf_win is not None and self._pf_win.isVisible():
            self._pf_win.raise_()                # zaten açık -> öne getir
            self._pf_win.activateWindow()
            return
        self._pf_win = PreflightWindow(vehicle=self.vehicle)
        self._pf_win.show()                      # modelsiz (non-modal) pencere
```

- `PreflightWindow` bir `QMainWindow`'dur; ayrı, bağımsız bir pencere açar.
- Host'un `QApplication`'ını kullanır — **ikinci `QApplication` oluşturma.**
- Pencereyi `self._pf_win`'de saklamazsan çöp toplayıcı kapatır.

---

## Adım 4 — Ayrı pencere yerine sekme/diyaloğa gömmek (alternatif)

Yeni bir pencere yerine kendi sekmene/diyaloğuna **widget** olarak gömmek
istersen `PreflightPage` (bir `QWidget`) kullan:

```python
from preflight_page import PreflightPage

page = PreflightPage(vehicle=self.vehicle)
self.tabWidget.addTab(page, "Uçuş Öncesi")
# ya da bir QDialog içine:
#   dlg = QDialog(self); QVBoxLayout(dlg).addWidget(page); dlg.show()
```

> Bu durumda yaşam döngüsü temizliğinden **sen** sorumlusun: kapanışta
> `page.shutdown()` çağır (poll zamanlayıcısını durdurur, dinleyiciyi kaldırır).
> `PreflightWindow` bunu `closeEvent`'inde kendiliğinden yapar.

---

## Adım 5 — Yaşam döngüsü / temizlik

- **`PreflightWindow`** kapatıldığında `closeEvent` → `page.shutdown()` otomatik
  çağrılır (poll durur, MAVLink dinleyicisi kaldırılır). Host `Vehicle`'ı
  kapatılmaz.
- **`PreflightPage`**'i elle gömdüysen, kapatırken kendin `page.shutdown()` çağır.
- Host uygulaman tamamen kapanırken dronekit bağlantısını sen kapat:
  `self.vehicle.close()`.

---

## Tam çalışan örnek

[`examples/host_app_ornegi.py`](examples/host_app_ornegi.py) — içinde tek butonlu
küçük bir "ana uygulama" ve o butona bağlı açılır `PreflightWindow` vardır.

```bash
# Host kendi dronekit bağlantısını kurup pencereye verir (gerçek senaryo):
python preflight_page/examples/host_app_ornegi.py --connect tcp:127.0.0.1:5760

# Bağlantı vermezsen pencere kendi bağlantı çubuğunu gösterir (host'ta Vehicle yokmuş gibi):
python preflight_page/examples/host_app_ornegi.py
```

---

## Dikkat edilecekler (sık hatalar)

| Sorun | Sebep / Çözüm |
|---|---|
| Pencere açılıp **hemen kapanıyor** | Referansı saklamadın. `self._pf_win = PreflightWindow(...)` yap (yerel değişken yetmez). |
| `ModuleNotFoundError: preflight_page` | Depo kökü `sys.path`'te değil. Adım 1. |
| `ModuleNotFoundError: PyQt5 / dronekit` | Host'un venv'ine kurulmamış. Önkoşullar. |
| `No module named 'past'` | `pip install future` (dronekit'in bağımlılığı). |
| İkinci kez tıklayınca **iki pencere** açılıyor | Tek-örnek korumasını ekle (Adım 3). |
| Host'ta **ikinci QApplication** hatası | Host'un mevcut `QApplication`'ını kullan; modülde yeni oluşturma. |
| Araç **ARM iken** bazı işlemler reddediliyor | Tasarım gereği: kalibrasyon/servo/yüzey testi yalnız DISARM'da çalışır. |
| Telemetri akış hızları değişti | Modül bağlanınca ihtiyaç duyduğu MAVLink akışlarını/aralıklarını ister (EKF/VIBRATION/BATTERY/POWER). Host'un kendi istekleriyle çakışmaz ama oranları etkileyebilir. |

> Güvenlik: Modül araca **asla arm komutu göndermez.** Komut/parametre yazan tek
> istisnalar operatörün kendi başlattığı işlemlerdir (kalibrasyon, servo trim,
> yüzey yön testi) — host uygulamanın bağlantısını bozmaz.
