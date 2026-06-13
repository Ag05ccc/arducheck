"""Maps a failed/warned check (or an autopilot PreArm message) to a concrete,
actionable Turkish remedy — "what to do to fix it". The Status Center shows
each problem together with its remedy.

remedy_for(check) is matched first by keywords in the check detail (this also
covers PreArm passthrough messages, whose text lands in the detail), then by
the check id prefix as a general fallback.
"""

# (keywords-any, remedy) — matched against the lowercased check detail.
# Order matters: most specific first.
KEYWORD_REMEDIES = [
    (["3d accel calibration", "accel calibration needed", "accels not calibrated",
      "ivmeölçer kalibrasyonu", "accelerometer"],
     "Sol paneldeki Kalibrasyon → İvmeölçer ile 6 pozisyonlu ivmeölçer "
     "kalibrasyonunu yapın, ardından otopilotu yeniden başlatın."),
    (["gyros not calibrated", "gyro calibration", "jiroskop"],
     "Kalibrasyon → Jiroskop ile kalibrasyon yapın (araç tamamen sabitken)."),
    (["compass not calibrated", "compass calibrated requires reboot",
      "compass offsets too high", "check mag field", "compasses inconsistent",
      "pusula kalibrasyon", "pusula ofset"],
     "Kalibrasyon → Pusula ile pusula kalibrasyonu yapın; metal/manyetik "
     "nesnelerden uzaklaşın. Kayıt sonrası otopilotu yeniden başlatın."),
    (["bad fix", "need 3d fix", "gps 1: not healthy", "gps fix", "3d fix bekleniyor",
      "waiting for home", "ev konumu ayarlanmamış", "gps and ahrs differ"],
     "Aracı açık gökyüzü altına çıkarın, GPS anteni bağlantısını kontrol edin ve "
     "3D kilit (≥ 8 uydu) için birkaç dakika bekleyin. Ev konumu kilit sonrası "
     "otomatik ayarlanır."),
    (["hdop", "zayıf gps", "uydu"],
     "Daha açık bir alana geçin, engellerden (bina/ağaç) uzaklaşın ve GPS "
     "geometrisinin iyileşmesini bekleyin."),
    (["not using configured ahrs", "ekf", "varyans", "konum kestirimi",
      "inconsistent", "vel error"],
     "Aracı sabit ve düz tutarak EKF'in oturmasını bekleyin (GPS kilidi "
     "gerekir). Titreşim yüksekse otopilot montajını kontrol edin."),
    (["airspeed", "hava hızı", "arspd"],
     "Kalibrasyon → Baro/Hava Hızı ile pitot kapalıyken sıfırlayın. Sensör "
     "tanımlı değilse ARSPD_TYPE'ı ayarlayın veya hava hızını devre dışı "
     "bırakın (ARSPD_USE=0)."),
    (["batt_monitor", "batarya izlenmiyor"],
     "BATT_MONITOR parametresini güç modülünüze göre ayarlayın (tipik: 4). "
     "Aksi halde batarya failsafe çalışmaz."),
    (["voltaj", "kritik eşik", "düşük voltaj", "hücre voltajı", "battery", "voltage"],
     "Bataryayı tam dolu bir bataryayla değiştirin veya şarj edin."),
    (["aşırı akım", "overcurrent"],
     "Güç modülünü ve servo/güç kablolamasını kontrol edin; kısa devre olabilir."),
    (["usb bağlı"],
     "Uçuştan önce USB kablosunu çıkarın; araç tezgah gücünde görünüyor."),
    (["rc girişi yok", "rc sinyali", "no rc"],
     "Kumandayı açın ve alıcının bağlı/eşleşmiş olduğundan emin olun."),
    (["rc kalibrasyon", "rc not calibrated", "rc3_min", "fabrika değer"],
     "Yer istasyonunuzda RC (Radio) kalibrasyonu yapın."),
    (["gaz", "throttle"],
     "Gaz kolunu en alt konuma (minimum) çekin."),
    (["thr_failsafe", "rc failsafe"],
     "THR_FAILSAFE=1 yapın; böylece RC bağlantısı koptuğunda failsafe devreye "
     "girer."),
    (["failsafe pwm", "thr_fs_value"],
     "THR_FS_VALUE değerini RC3_MIN'in en az 40 µs altına ayarlayın."),
    (["uzun failsafe", "kısa failsafe", "fs_long", "fs_short", "failsafe eylem"],
     "Failsafe eylemini güvenli bir değere ayarlayın (genellikle RTL=1)."),
    (["batarya failsafe eşik", "kritik eşik düşük"],
     "Batarya failsafe eşiklerini düzeltin: kritik voltaj, düşük voltajdan "
     "küçük olmalı."),
    (["arm edilebilir", "kontrolleri geçmiyor", "pre-arm kontrolleri geçmiyor"],
     "Aşağıda listelenen pre-arm hatalarını giderin; her biri çözülünce "
     "otopilot bir sonrakini gösterebilir, tekrar çalıştırın."),
    (["arming_check", "skipchk", "kontroller kapalı", "atlanan kontroller",
      "tüm kontroller etkin olmalı"],
     "ARMING_CHECK=1 yapın (tüm pre-arm kontrolleri etkin olmalı)."),
    (["arming_require", "açılışta armli", "boots armed"],
     "ARMING_REQUIRE=1 yapın; araç açılışta armlı başlamamalı."),
    (["fence", "geofence"],
     "Geofence ayarlarını kontrol edin: FENCE_ACTION ≠ 0 ve tip ile yarıçap/"
     "irtifa değerleri tutarlı olmalı."),
    (["yatış limiti", "roll_limit"],
     "ROLL_LIMIT_DEG'i makul bir değere ayarlayın (30–45°)."),
    (["tırmanamaz", "burun", "ptch_lim", "yunuslama"],
     "Yunuslama limitlerini düzeltin (PTCH_LIM_MAX_DEG pozitif, "
     "PTCH_LIM_MIN_DEG negatif olmalı)."),
    (["hız aralığı", "seyir hızı", "airspeed_min", "airspeed_max"],
     "Hava hızı zarfını düzeltin: min < seyir < maks olmalı ve min ≥ stall hızı."),
    (["titreşim", "clipping", "vibration"],
     "Otopilot titreşim sönümleme montajını kontrol edin; pervane dengesini "
     "ve gevşek bağlantıları gözden geçirin."),
    (["kayıt", "logging", "sd"],
     "SD kartı kontrol edin: doğru takılı ve biçimlendirilmiş olmalı; gerekirse "
     "yenisiyle değiştirin."),
    (["safety", "emniyet anahtar"],
     "Emniyet anahtarına basın (LED sabit yanmalı)."),
    (["crashdump", "internal error", "dahili hata"],
     "Otopilotu yeniden başlatın. Sorun sürerse firmware'i yeniden yükleyin ve "
     "logu ArduPilot topluluğuna bildirin."),
    (["paket kayb", "drop", "bağlantı güvenilmez"],
     "Telemetri bağlantısını iyileştirin (USB kullanın, antenleri kontrol edin)."),
    (["parametre listesi tam"],
     "Bağlantıyı iyileştirip (USB tercih edin) kontrolleri yeniden çalıştırın."),
    (["araç arm edilmiş", "armli"],
     "Aracı disarm edin; kontroller yalnızca disarm durumda yapılmalıdır."),
    (["heartbeat", "kalp atış"],
     "Aracın açık olduğundan ve bağlantı portu/hızının doğru olduğundan emin "
     "olun."),
]

# Fallback by check id prefix.
ID_REMEDIES = [
    ("PRE", "Otopilotun bildirdiği pre-arm hatasını giderin, sonra kontrolleri "
            "yeniden çalıştırın."),
    ("SENS", "Otopilotu yeniden başlatın; sorun sürerse ilgili sensör donanımını "
             "kontrol edin."),
    ("MAG", "Kalibrasyon → Pusula ile pusula kalibrasyonu yapın."),
    ("INS", "Kalibrasyon → İvmeölçer/Jiroskop ile kalibrasyon yapın."),
    ("GPS", "Açık alana çıkıp GPS kilidini bekleyin."),
    ("EKF", "Aracı sabit tutup EKF'in oturmasını bekleyin."),
    ("BAT", "Batarya ve güç sistemini kontrol edin."),
    ("RC", "Kumanda ve RC ayarlarını kontrol edin."),
    ("FS", "Failsafe parametrelerini gözden geçirin."),
    ("ARM", "Arming parametrelerini düzeltin."),
    ("FNC", "Geofence yapılandırmasını kontrol edin."),
    ("ENV", "Uçuş zarfı parametrelerini düzeltin."),
    ("ASP", "Hava hızı sensörü ayarlarını/kalibrasyonunu kontrol edin."),
    ("VIB", "Otopilot montajını ve titreşim kaynaklarını kontrol edin."),
    ("LOG", "SD kart / kayıt sistemini kontrol edin."),
    ("CONN", "Bağlantıyı ve aracın durumunu kontrol edin."),
]


def remedy_for(check):
    if check.get("status") not in ("FAIL", "WARN"):
        return ""
    detail = (check.get("detail") or "").lower()
    name = (check.get("name") or "").lower()
    hay = detail + " " + name
    for kws, text in KEYWORD_REMEDIES:
        if any(k in hay for k in kws):
            return text
    cid = check.get("id") or ""
    for prefix, text in ID_REMEDIES:
        if cid.startswith(prefix):
            return text
    return ""
