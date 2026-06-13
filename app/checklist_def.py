"""Manual preflight checklist (operator tick-boxes), based on the ArduPilot
plane first-flight and preflight documentation. These cannot be verified
over telemetry; the operator confirms each one."""

MANUAL_ITEMS = [
    {"section": "Güç vermeden önce", "items": [
        {"id": "MAN-01", "text": "Ağırlık merkezi (CG) gövde spesifikasyonuna "
         "göre doğrulandı (emin değilseniz hafif burun ağırlıklı tercih edin)."},
        {"id": "MAN-02", "text": "Pitot tüpü güce vermeden ÖNCE gevşekçe "
         "kapatıldı (hava hızı sensörü yoksa atlayın)."},
        {"id": "MAN-03", "text": "Kumanda AÇIK ve MANUAL modda; araca güç "
         "bundan sonra verildi."},
        {"id": "MAN-04", "text": "Pervane cıvataları/spinner sıkı; pervanede "
         "çatlak/kırık yok; motor montajı sağlam."},
        {"id": "MAN-05", "text": "Gövde/bağlantı kontrolü: menteşeler, kontrol "
         "hornları, klipsler, servo vidaları, kanat cıvataları, kapaklar."},
        {"id": "MAN-06", "text": "Batarya sabitlendi (kayış/cırt), konnektör "
         "tam oturdu, bataryada şişme yok."},
    ]},
    {"section": "Güç verdikten sonra", "items": [
        {"id": "MAN-07", "text": "Güç verildikten sonra araç ~30 sn hareketsiz "
         "tutuldu (jiroskop kalibrasyonu için)."},
        {"id": "MAN-08", "text": "Elektronik en az 1 dk ısındı (soğuk havada "
         "daha uzun) — hava hızı kalibrasyonundan önce."},
        {"id": "MAN-09", "text": "Hava hızı sıfır kalibrasyonu pitot kapalıyken "
         "yapıldı; üfleme testiyle sensör tepkisi doğrulandı."},
        {"id": "MAN-10", "text": "PİTOT KAPAĞI ÇIKARILDI! (Kapakla uçuş ölümcül "
         "hatadır.)"},
    ]},
    {"section": "Kontrol yüzeyleri — her uçuşta", "items": [
        {"id": "MAN-11", "text": "MANUAL modda kumanda kontrolü: her yüzey "
         "DOĞRU yönde, tam hareketli, takılma yok, ters kanal yok."},
        {"id": "MAN-12", "text": "FBWA stabilizasyon yön kontrolü: araç elle "
         "yatırıldığında/kaldırıldığında yüzeyler aracı DÜZELTECEK yönde "
         "hareket ediyor. (Ters kanal en sık düşme nedenidir!)"},
        {"id": "MAN-13", "text": "FBWA seviye kontrolü: araç düz, kumanda "
         "ortada iken yüzeyler nötre yakın."},
    ]},
    {"section": "Bağlantı ve failsafe — günün ilk uçuşu / RC değişikliği sonrası", "items": [
        {"id": "MAN-14", "text": "RC menzil testi yapıldı; telemetri menzili "
         "görev için yeterli."},
        {"id": "MAN-15", "text": "Kumanda kapatma failsafe testi: TX kapatınca "
         "kısa failsafe (CIRCLE vb.), süre sonunda uzun failsafe (RTL vb.) "
         "modu devreye girdi; TX açılınca kontrol geri geldi."},
        {"id": "MAN-16", "text": "Alıcı sinyal kaybında 'sinyal yok' (no pulses) "
         "verecek şekilde ayarlı (sabit değer tutmuyor)."},
    ]},
    {"section": "Son kontroller", "items": [
        {"id": "MAN-17", "text": "Emniyet anahtarına basıldı / devre dışı "
         "(takılıysa) — LED sabit."},
        {"id": "MAN-18", "text": "Kalkış alanı temiz; çevredekiler pervane "
         "düzleminden uzak; rüzgar limitler içinde."},
        {"id": "MAN-19", "text": "İlk uçuş modu mantıklı seçildi (ilk uçuşta "
         "MANUAL/FBWA); mod anahtarı konumları doğrulandı."},
    ]},
]
