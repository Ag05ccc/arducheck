# ✈ ArduCheck — ArduPilot Uçuş Öncesi Kontrol Uygulaması

ArduPilot otopilotlu uçaklar için **uçuş öncesi tüm kontrollerin** ve
**kalibrasyonların** tek ekrandan yapılmasını sağlayan, harita ve uçuş
göstergeli (HUD) tam-ekran bir yer kontrol istasyonu uygulaması. ArduPilot'un
resmi dokümanlarındaki pre-arm / preflight önerilerine göre hazırlanmıştır.

- **Ubuntu ve Windows**'ta çalışır (Python 3.8+)
- Bağımlılıklar yalnızca `pymavlink` + `pyserial`; harita kütüphanesi (Leaflet)
  uygulamayla birlikte gelir (internet gerekmez, çevrimdışı ızgaraya düşer)
- Arayüz tarayıcıda açılır — ek GUI kütüphanesi/derleme adımı yok
- Seri port (USB/telemetri), UDP ve TCP/SITL bağlantısı; portlar otomatik bulunur
- **Durum odaklı tam-ekran panel:** ekranın merkezinde tek soru — *"her şey
  tamam mı?"* Büyük **✓ HER ŞEY TAMAM** / **✘ N SORUN ÇÖZÜLMELİ** kararı; sorun
  varsa her biri **sorun + somut çözüm önerisi** kartı olarak gösterilir
  (örn. "GPS fix yok → Aracı açık gökyüzü altına çıkarın, kilidi bekleyin")
- **Yardımcı paneller:** sol tarafta durum kutucukları + kalibrasyon; sağ tarafta
  **boyutu ayarlanabilir küçük harita** (Gizle / Küçük / Orta / Büyük) — araç
  konumu/izi/geofence/HUD — ve manuel kontrol listesi; üst bardaki canlı
  **UÇUŞA HAZIR / HAZIR DEĞİL** kararı, otopilot mesaj şeridi; açık/koyu tema,
  mobil/dar ekran uyumlu (durum merkezi en üstte)
- Araca **asla** arm komutu göndermez. Araca komut/parametre yazan tek
  istisnalar, operatörün kendisinin başlattığı işlemlerdir: kalibrasyon
  komutları, **Servo** sekmesindeki trim ayarı (`SERVOn_TRIM` yazar) ve
  yüzey yön testi (DISARM + MANUAL şartıyla ~1.5 sn RC override gönderir).

## Servo & Parametre araçları

- **Servo sekmesi** — *Yüzey Yön Testi*: DISARM + MANUAL modda, karışım
  (mixing) üzerinden RC override darbesi gönderir; "Burun yukarı" verildiğinde
  elevatör/elevonların ikisi de yukarı kalkmalı, "Sağa yatış"ta sağ kanatçık
  yukarı / sol aşağı. Operatör gözle doğrular; ters çalışan yüzey
  `SERVOn_REVERSED` ile düzeltilir. *Servo Trim*: yüzey servoları
  (SERVOn_FUNCTION'a göre) listelenir, ±1/±10 adımlarla `SERVOn_TRIM`
  otopilota yazılır (kanatçıkların nötrde düz durması için).
- **Param sekmesi** — `ref_params.param` referans dosyası: aracın mevcut
  parametrelerinden kaydedilebilir ya da hazır bir .param dosyası (Mission
  Planner / QGC biçimi) yüklenebilir. Farklar hem kontrol koşusunda
  ("Parametre Referansı" grubu) hem de "Farkları şimdi göster" ile anlık
  listelenir.

## Kalibrasyon (adım adım sihirbaz)

Sol paneldeki **Kalibrasyon** sekmesinden, yalnızca araç **DISARM** iken:

| Kalibrasyon | Nasıl çalışır |
|---|---|
| **Pusula** | Onboard mag kalibrasyonu; aracı tüm eksenlerde çevirirsiniz, **canlı yüzde çubuğu** ilerler, bitince "Kaydet" ile saklanır (otopilot yeniden başlatılmalı) |
| **İvmeölçer (6 pozisyon)** | Otopilotun istediği her pozisyon (düz / sol / sağ / burun aşağı / burun yukarı / sırtüstü) adım adım gösterilir; aracı o konuma getirip "Bu pozisyondayım" dersiniz |
| **Yatay (Level)** | Aracı düz koyup tek tıkla |
| **Jiroskop** | Aracı sabit tutup tek tıkla |
| **Baro / Hava Hızı** | Yer basıncı + hava hızı sıfır kalibrasyonu |

> Not: Pusula ve ivmeölçer kalibrasyonu, aracın fiziksel olarak döndürülmesini
> gerektirir; bu nedenle hareketsiz SITL simülasyonunda **tamamlanamaz** (her
> yer kontrol istasyonu için geçerli bir simülatör kısıtı). Gerçek donanımda
> aracı yönlendirdiğinizde tamamlanır.

## Neleri kontrol eder?

**Otomatik (telemetri + parametreler):**

| Grup | İçerik |
|---|---|
| Bağlantı / Sistem | heartbeat, araç/firmware tipi ve sürümü, arm durumu, paket kaybı, CPU yükü, dahili hata/CrashDump |
| Otopilot Pre-Arm | `MAV_CMD_RUN_PREARM_CHECKS` ile otopilotun kendi kontrolleri zorla çalıştırılır, tüm `PreArm:` mesajları aynen raporlanır |
| Sensör Sağlığı | SYS_STATUS bitleri: jiroskop, ivmeölçer, pusula, baro, hava hızı, GPS, RC, AHRS, batarya, kayıt, fence |
| EKF | temel kestirimler, mutlak konum, glitch/başlatma durumu, varyanslar (0.5/0.8 eşikleri) |
| GPS | fix tipi, HDOP (`GPS_HDOP_GOOD`), uydu sayısı, çift GPS tutarlılığı, ev konumu |
| Pusula | kalibrasyon ofsetleri (büyüklük < 500/600), soft-iron ölçekleri, alan/tutarlılık mesajları |
| İvmeölçer/Jiro | kalibrasyon ofset/ölçek doğrulama, tutarlılık mesajları, araç hareketsizliği |
| Hava Hızı | ARSPD_TYPE/USE uyumu, RATIO kalibrasyonu, durağan okuma, AUTOCAL uyarısı |
| Batarya / Güç | voltaj/hücre, failsafe eşikleriyle karşılaştırma, kalan kapasite, arıza bitleri, kart 5V hattı, USB uyarısı |
| RC | sinyal, kalibrasyon, trim aralıkları, kanal değerleri, gaz konumu, RSSI |
| Failsafe | THR_FAILSAFE, kısa/uzun eylem ve süreleri, batarya failsafe eşik/eylemleri, GCS failsafe |
| Arming | ARMING_CHECK / ARMING_SKIPCHK, ARMING_REQUIRE, rudder disarm riski |
| Geofence | eylem, tip-yapılandırma uyumu, irtifa sınırları, RTL-fence çelişkisi |
| Uçuş Zarfı | AIRSPEED_MIN/MAX/CRUISE tutarlılığı, yatış/yunuslama limitleri, RTL irtifası |
| Titreşim | seviye (30/60 m/s² eşikleri), ivmeölçer doyması (clipping) |
| Kayıt | logging sağlığı, SD kart mesajları |

**Manuel (operatör onaylı):** CG, pitot kapağı, pervane/gövde kontrolü,
kontrol yüzeyi yön testleri (MANUAL + FBWA), menzil ve failsafe testi,
kalkış alanı — tik kutulu liste, rapora dahil edilir.

Sonuçlar **GEÇTİ / UYARI / HATA** olarak gruplanır; tek tıkla yazdırılabilir
HTML rapor alınır.

## Kurulum

### Ubuntu

```bash
./scripts/kur_ubuntu.sh      # sanal ortam + pymavlink + dialout grubu
./scripts/baslat_ubuntu.sh   # uygulamayı başlatır, tarayıcı otomatik açılır
```

### Windows

1. [python.org](https://www.python.org/downloads/) üzerinden Python 3 kurun
   (**"Add Python to PATH" işaretli olmalı**).
2. `scripts\kur_windows.bat` dosyasına çift tıklayın.
3. `scripts\baslat_windows.bat` ile başlatın.

Elle kurulum (her iki sistemde):

```bash
pip install pymavlink pyserial
python3 arducheck.py
```

> Harita kütüphanesi (Leaflet) `web/vendor/` içinde uygulamayla birlikte gelir;
> internet varken OpenStreetMap karoları, yokken koyu ızgara gösterilir. İnternet
> olmadan da araç konumu/iz/geofence/HUD çalışmaya devam eder.

## Kullanım

1. Uygulamayı başlatın — tarayıcıda `http://127.0.0.1:8642` açılır.
2. **Bağlantı:** USB kablo → seri port listesinden seçin (uçuş kartları ✈ ile
   işaretlenir, USB için 115200). Telemetri telsizi → 57600. Yer istasyonu
   yayını → UDP 14550. SITL → TCP 127.0.0.1:5760.
3. **▶ Tüm Kontrolleri Çalıştır** — ~15 sn sürer; telemetri toplanır,
   otopilotun pre-arm kontrolleri tetiklenir, sonuçlar gruplu listelenir.
4. Hataları giderin, tekrar çalıştırın. (Otopilot pre-arm kontrolleri sırayla
   yapar: bir hata düzelince yenisi görünebilir.)
5. **Manuel listeyi** (sağ panel) fiziksel kontrolleri yaparak işaretleyin.
6. **⬇ Rapor** ile yazdırılabilir HTML raporu indirin.
7. Gerekirse sol **Kalibrasyon** sekmesinden pusula/ivmeölçer/level/jiroskop/baro
   kalibrasyonunu adım adım yapın (araç disarm iken).

Üst bardaki renkli karara tıklayarak başarısız/uyarı veren kontrollerin
ayrıntısını açan **uçuş öncesi özet penceresini** görebilirsiniz.

### SITL ile deneme

Hazır yardımcı betikle (ArduPilot `~/ardupilot` altında derliyse):

```bash
./scripts/sitl_baslat.sh   # TCP 127.0.0.1:5760 açar (ARDUPILOT=/yol ile özelleştirin)
# ArduCheck'te: TCP / SITL sekmesi → 127.0.0.1:5760 → Bağlan
```

Ya da elle:

```bash
cd ~/ardupilot
./build/sitl/bin/arduplane --model plane \
    --defaults Tools/autotest/models/plane.parm \
    --home -35.363261,149.165230,584,353
```

## Sınırlamalar

Telemetri kontrolleri şunları **tespit edemez:** tek kanal RC arızası,
ters bağlı kontrol yüzeyi, motor/pervane mekanik sorunları, pitot kapağının
fiziksel durumu. Bunlar manuel listededir. Nihai uçuş kararı pilota aittir.

## Dosya yapısı

```
arducheck.py            # giriş noktası (app paketini başlatır)
requirements.txt        # bağımlılıklar (pymavlink, pyserial)
app/                    # uygulama paketi
  core/                 # web ve PyQt arayüzlerinin paylaştığı operasyon katmanı
  server.py             # stdlib HTTP sunucu + JSON API
  mavlink_client.py     # MAVLink bağlantı/telemetri katmanı
  checks.py             # kontrol motoru (katalog + eşikler) + telemetri özeti
  calibration.py        # adım adım kalibrasyon motoru (pusula/ivmeölçer/level/jiro/baro)
  checklist_def.py      # manuel kontrol listesi tanımı
  remedies.py           # her kontrol için somut çözüm önerileri
  report.py             # HTML rapor üretici
  web/                  # tam-ekran arayüz (HTML/CSS/JS, framework yok)
    vendor/leaflet/     # yerelden sunulan harita kütüphanesi (offline-uyumlu)
    vendor/icons/       # araç ikonu + çevrimdışı karo
scripts/                # kurulum & başlatma yardımcıları
  kur_ubuntu.sh / kur_windows.bat       # kurulum
  baslat_ubuntu.sh / baslat_windows.bat # başlatma
  sitl_baslat.sh                        # SITL simülatörü
```

> `ref_params.param` (Param sekmesinden kaydedilen referans) depo kökünde
> oluşturulur ve sürüm kontrolüne dahil edilmez.
