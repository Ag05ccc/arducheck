# preflight_page — Gömülebilir PyQt5 Uçuş Öncesi Kontrol Modülü

ArduCheck'in **tüm** uçuş öncesi kontrol, kalibrasyon, servo/parametre aracı ve
raporlama özelliklerini, mevcut bir **PyQt5** GUI uygulamasına ayrı bir pencere
(ya da gömülü bir sayfa) olarak ekleyebileceğiniz tek bir modül hâline getirir.
Uçak bağlantısı **dronekit** `Vehicle` üzerinden sağlanır; tüm MAVLink işlemleri
o bağlantı üzerinden yapılır.

> Web arayüzü ve PyQt sayfası eşit arayüzlerdir. İkisi de kontrol,
> kalibrasyon, rapor ve ortak operasyon davranışını `app/` altındaki çekirdek
> modüllerden kullanır; servo/parametre güvenlik kapıları, yüzey testi ve state
> payload mantığı `app.core.preflight` içinde ortak tutulur.

## Mimari

```
host GUI (PyQt5)                    preflight_page
 └─ dronekit Vehicle  ───────────►  DronekitMavClient   (app.MavClient'i besler)
                                          │  (get_msg / get_param / run_command…)
                                          ▼
                              app.core.preflight + app.checks/calibration/report
                                          ▲
                                     PreflightController  (Qt sinyalleri + poll)
                                          ▲  sinyaller / metotlar
                                     PreflightPage (QWidget) + widgets/*
```

- **`DronekitMavClient`** — `app.mavlink_client.MavClient`'i alt sınıflar; kendi
  okuma thread'ini başlatmaz, mesajları dronekit'in `add_message_listener('*')`
  akışından besler, komutları Vehicle'ın master MAVLink bağlantısından gönderir.
  Böylece STATUSTEXT birleştirme, parametre indirme, COMMAND_ACK eşleştirme gibi
  incelikli mantık **yeniden yazılmaz**.
- **`app.core.preflight`** — web ve PyQt için ortak operasyon katmanı: busy
  kilidi, state payload, servo yüzey testi, DISARM kapısı, parametre referansı
  ve rapor üretimi burada tutulur.
- **`PreflightController`** — Qt'ye özgü bağlantı/poll/sinyal köprüsü. Hiçbir
  widget tanımaz; ortak core servislerini çağırır ve sinyaller yayar.
- **`PreflightPage` / `PreflightWindow`** — arayüz. Tarayıcı yerine saf Qt
  widget'ları (harita yerine native telemetri paneli).

## Kullanım

### 1) Host uygulamaya gömme (önerilen)

> 📌 Başka bir PyQt5 uygulamasına **açılır pencere** olarak ekleme adımlarının
> tamamı (yol ayarı, tek-örnek koruması, yaşam döngüsü, sık hatalar) için ayrıntılı
> kılavuza bakın: **[ENTEGRASYON.md](ENTEGRASYON.md)** + çalışan örnek
> [`examples/host_app_ornegi.py`](examples/host_app_ornegi.py).

Host uygulamada dronekit bağlantısı zaten kuruluysa, o `Vehicle`'ı doğrudan verin:

```python
from preflight_page import PreflightPage, PreflightWindow

# a) Ayrı pencere olarak aç:
self.preflight_win = PreflightWindow(vehicle=self.vehicle)
self.preflight_win.show()

# b) Ya da kendi diyaloğunuza/sekmenize gömün:
page = PreflightPage(vehicle=self.vehicle)
some_layout.addWidget(page)
```

Modül bağlantının **sahibi değildir**: pencere kapanırken yalnızca kendi mesaj
dinleyicisini kaldırır, `Vehicle`'ı kapatmaz. Pencere kapanışında temizlik için:

```python
page.shutdown()          # PreflightWindow bunu closeEvent'te kendi yapar
```

### 2) Bağımsız (tek-pencere) test

dronekit bağlantısını modülün kendisi kursun:

```bash
python -m preflight_page                              # bağlantıyı arayüzden kur
python -m preflight_page --connect tcp:127.0.0.1:5760 # SITL
python -m preflight_page --connect /dev/ttyACM0 --baud 115200
python -m preflight_page --connect udp:0.0.0.0:14550
```

Bağımsız modda `Vehicle`'ın sahibi modüldür; pencere kapanınca bağlantı kapatılır.

## Özellikler (web sürümüyle tam parite)

- **Otomatik kontroller** (`app.checks.run_all`): bağlantı/sistem, otopilot
  pre-arm (MAV_CMD 401), sensör sağlığı, EKF, GPS, pusula, INS, hava hızı,
  batarya, RC, failsafe, arming, geofence, uçuş zarfı, parametre referansı,
  titreşim, kayıt — 17 grup, GEÇTİ/UYARI/HATA kararı ve her soruna **çözüm önerisi**.
- **Kalibrasyon sihirbazları**: pusula (canlı %), ivmeölçer (6 pozisyon),
  yatay, jiroskop, baro/hava hızı.
- **Servo araçları**: yüzey trim (`SERVOn_TRIM` yazar) ve MANUAL modda yüzey
  yön testi (RC override, disarm şartıyla, supersede destekli).
- **Parametre referansı**: mevcut parametreleri kaydet, .param dosyası yükle,
  araç↔referans farklarını listele.
- **Manuel kontrol listesi** (operatör onaylı maddeler) + **HTML rapor**.
- **Canlı telemetri paneli** (native): mod, arm, GPS, konum, EKF, batarya, Vcc,
  titreşim, RC, attitude, hava hızı, pre-arm + otopilot mesaj şeridi.

> Araca **asla** arm komutu gönderilmez. Komut/parametre yazan tek istisnalar
> operatörün kendi başlattığı işlemlerdir (kalibrasyon, servo trim, yüzey testi) —
> web sürümüyle aynı güvenlik sözleşmesi.

## Kurulum

```bash
pip install -r preflight_page/requirements.txt
```

- **Python 3.10+ notu:** dronekit 2.9.2, kaldırılmış `collections.MutableMapping`
  takma adlarını kullanır ve `past` (=`future` paketi) ister. Modül, dronekit'i
  import etmeden önce gerekli `collections` şimini **otomatik** uygular; sadece
  `future` paketinin kurulu olması yeterlidir (requirements'a dahildir).
- `preflight_page` paketi depo kökünde `app/` paketinin yanında durmalıdır
  (motorları oradan import eder; kök otomatik `sys.path`'e eklenir).

## SITL ile deneme

```bash
scripts/sitl_baslat.sh            # TCP 127.0.0.1:5760
python -m preflight_page --connect tcp:127.0.0.1:5760
```

> Not: Pusula/ivmeölçer kalibrasyonu fiziksel döndürme gerektirdiğinden sabit
> SITL'de tamamlanmaz (her yer istasyonu için geçerli simülatör kısıtı).
