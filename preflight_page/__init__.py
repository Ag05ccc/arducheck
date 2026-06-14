"""ArduCheck — gömülebilir PyQt5 uçuş öncesi kontrol modülü (preflight_page).

Mevcut ArduCheck kontrol/kalibrasyon/rapor motorlarını (``app`` paketi) HİÇ
değiştirmeden yeniden kullanır; tek fark uçak bağlantısının **dronekit**
``Vehicle`` üzerinden sağlanması ve arayüzün tarayıcı yerine **Qt** olmasıdır.

İki kullanım biçimi vardır:

1. Gömülü (host GUI'ye entegre) — bağlantı host uygulamada zaten dronekit ile
   kurulmuştur; o ``Vehicle`` doğrudan verilir::

       from preflight_page import PreflightPage, PreflightWindow
       page = PreflightPage(vehicle=mevcut_dronekit_vehicle)   # bir QWidget
       layout.addWidget(page)
       # veya ayrı pencere olarak:
       win = PreflightWindow(vehicle=mevcut_dronekit_vehicle); win.show()

2. Bağımsız (tek-pencere) test — bağlantıyı modülün kendisi kurar::

       python -m preflight_page --connect tcp:127.0.0.1:5760

Host uygulama ``Vehicle``'ı verdiğinde modül bağlantının SAHİBİ DEĞİLDİR:
kapanırken yalnızca kendi dinleyicisini kaldırır, ``Vehicle``'ı kapatmaz.
"""

__all__ = ["PreflightPage", "PreflightWindow"]


def __getattr__(name):
    # Tembel içe aktarma (PEP 562): ``preflight_page.dronekit_adapter`` gibi alt
    # modüller, ağır Qt bağımlılığını ya da page.py'yi çekmeden import edilebilsin.
    if name in __all__:
        from . import page
        return getattr(page, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
