"""Hardware abstraction layer.

Two devices are wired to this trolley and nothing else:

  * scale.py    4 x 50 kg load cells -> HX711 -> Pi (GPIO 5 / GPIO 6)
  * scanner.py  camera barcode/QR reader (the USB scanner gun is a keyboard,
                so it is captured in the browser, not here)

A third device is optional — the thermal receipt printer:

  * printer.py  ESC/POS thermal printer over USB, Bluetooth, network, or the
                cloud device bridge

Every module exposes the same interface whether it is driving real hardware on
the Pi, reading through the cloud device bridge, or simulating on a laptop. The
choice comes from `settings.mode`, so application code never imports lgpio or
opencv directly.
"""
