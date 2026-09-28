"""Load cell weight reader — 4 x 50 kg cells -> HX711 -> Raspberry Pi 5.

Three implementations, one interface
-----------------------------------
HX711Scale     bit-bangs the HX711 protocol over `lgpio` (Pi 5 native).
RemoteScale    used when the app runs in the CLOUD. Raw counts arrive from
               `pi_agent.py` through `app.devices.bridge`; all calibration
               maths still happens here, so calibrating from the settings UI
               takes effect immediately.
SimulatedScale pure software scale for laptop development / demos.

Wiring (four 50 kg half-bridge cells combined into one full Wheatstone bridge)
-----------------------------------------------------------------------------
Each 50 kg cell has 3 wires. Combine the four cells so opposite corners form
the bridge (a combinator board does this for you), then:

    Bridge  ->  HX711 : E+  E-  A+  A-
    HX711 VCC         -> Pi 3V3 (pin 1) or 5V (pin 2)   see note below
    HX711 GND         -> Pi GND (pin 6)
    HX711 DT  (DOUT)  -> BCM GPIO 5  (pin 29)   HX711_DT_PIN
    HX711 SCK (PD_SCK)-> BCM GPIO 6  (pin 31)   HX711_SCK_PIN

Note on VCC: the Pi's GPIO pins are 3.3 V. Powering the HX711 from 5 V makes
its DOUT swing to ~5 V, which is out of spec for the Pi. Either power the HX711
from 3V3 (simplest, slightly noisier) or keep 5 V and put a level shifter (or a
1k/2k divider on DOUT) between DOUT and GPIO 5. Never feed 5 V logic straight
into a Pi input.

No buzzer or LEDs are used on this trolley — scan feedback is on screen.
"""
from __future__ import annotations

import statistics
import threading
import time

from app.config import settings


class BaseScale:
    """Common calibration maths shared by every scale implementation."""

    #: shown in the UI / diagnostics
    kind = "base"
    #: does this reading path involve a real load cell?
    real_hardware = False

    def __init__(self) -> None:
        self._reference_unit = settings.scale_reference_unit or 1.0
        self._offset = settings.scale_offset
        self._calibration_source = "env"

    # -- calibration -------------------------------------------------------
    def set_calibration(
        self, reference_unit: float, offset: float, source: str = "runtime"
    ) -> None:
        self._reference_unit = reference_unit or 1.0
        self._offset = offset
        self._calibration_source = source

    @property
    def calibration(self) -> dict:
        return {
            "reference_unit": self._reference_unit,
            "offset": self._offset,
            "source": self._calibration_source,
        }

    # -- reading -----------------------------------------------------------
    def read_raw(self) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def raw_to_grams(self, raw: float) -> float:
        return round((raw - self._offset) / (self._reference_unit or 1.0), 1)

    def get_grams(self, samples: int = 8) -> float:
        """Current basket weight in grams (median filtered)."""
        readings = []
        for _ in range(max(1, samples)):
            readings.append(self.read_raw())
            time.sleep(0.005)
        return self.raw_to_grams(statistics.median(readings))

    def read_stats(self, samples: int = 15) -> dict:
        """Raw + grams with a noise figure, for the calibration screens."""
        raws = []
        for _ in range(max(2, samples)):
            raws.append(self.read_raw())
            time.sleep(0.005)
        median = statistics.median(raws)
        return {
            "raw": round(median, 1),
            "grams": self.raw_to_grams(median),
            "raw_stdev": round(statistics.pstdev(raws), 2),
            "raw_min": round(min(raws), 1),
            "raw_max": round(max(raws), 1),
            "samples": len(raws),
        }

    def tare(self, samples: int = 15) -> float:
        """Reset the zero point to the current load. Returns the new offset."""
        readings = [self.read_raw() for _ in range(max(1, samples))]
        self._offset = statistics.median(readings)
        return self._offset

    # -- health ------------------------------------------------------------
    def health(self) -> dict:
        return {
            "kind": self.kind,
            "real_hardware": self.real_hardware,
            "online": True,
            "detail": "",
            **self.calibration,
        }

    def close(self) -> None:
        pass


class SimulatedScale(BaseScale):
    """Software scale used on a laptop / when no HX711 is present."""

    kind = "simulated"
    real_hardware = False

    def __init__(self, detail: str = "") -> None:
        super().__init__()
        self._reference_unit = 1.0
        self._offset = 0.0
        self._calibration_source = "simulated"
        self._grams = 0.0
        self._detail = detail
        self._lock = threading.Lock()

    def read_raw(self) -> float:
        # raw == grams in the simulator (reference_unit=1, offset=0)
        with self._lock:
            return self._grams

    # calibration is meaningless here; keep the identity transform
    def set_calibration(self, reference_unit: float, offset: float, source: str = "runtime") -> None:
        return

    # --- hooks the app uses to mimic putting items in the basket ---
    def sim_add_weight(self, grams: float) -> None:
        with self._lock:
            self._grams = max(0.0, self._grams + grams)

    def sim_set_weight(self, grams: float) -> None:
        with self._lock:
            self._grams = max(0.0, grams)

    def health(self) -> dict:
        return {**super().health(), "detail": self._detail or "software scale"}


class HX711Scale(BaseScale):
    """Real HX711 driver built on lgpio (Raspberry Pi 5 / RP1 compatible).

    The legacy RPi.GPIO-based HX711 libraries do not work on the Pi 5, so the
    24-bit protocol is bit-banged here directly.
    """

    kind = "hx711"
    real_hardware = True

    #: extra SCK pulses after the 24 data bits select the input & gain
    _GAIN_PULSES = {128: 1, 64: 3, 32: 2}

    def __init__(self, dt_pin: int | None = None, sck_pin: int | None = None,
                 gain: int | None = None) -> None:
        super().__init__()
        import lgpio  # type: ignore[import-not-found,import-untyped]  # imported lazily so laptops don't need it

        self._lgpio = lgpio
        self._dt = settings.hx711_dt_pin if dt_pin is None else dt_pin
        self._sck = settings.hx711_sck_pin if sck_pin is None else sck_pin
        gain = settings.hx711_gain if gain is None else gain
        self._gain_pulses = self._GAIN_PULSES.get(gain, 1)
        self._gain = gain

        try:
            self._chip = lgpio.gpiochip_open(0)
        except Exception as exc:
            raise RuntimeError(f"cannot open gpiochip0: {exc}") from exc
        try:
            lgpio.gpio_claim_input(self._chip, self._dt)
            lgpio.gpio_claim_output(self._chip, self._sck, 0)
        except Exception as exc:
            lgpio.gpiochip_close(self._chip)
            raise RuntimeError(
                f"GPIO {self._dt}/{self._sck} unavailable ({exc}). "
                "Another process (the app or pi_agent) probably owns the pins."
            ) from exc

        self._lock = threading.Lock()
        time.sleep(0.2)
        self.reset()

    # -- protocol ----------------------------------------------------------
    def _pulse(self) -> None:
        self._lgpio.gpio_write(self._chip, self._sck, 1)
        self._lgpio.gpio_write(self._chip, self._sck, 0)

    def _wait_ready(self, timeout_s: float = 1.0) -> None:
        start = time.time()
        while self._lgpio.gpio_read(self._chip, self._dt) == 1:
            if time.time() - start > timeout_s:
                raise TimeoutError(
                    "HX711 not responding: DOUT stuck high. Check VCC/GND, the "
                    f"DT wire on GPIO {self._dt} and the load-cell bridge."
                )
            time.sleep(0.001)

    def _read_once(self) -> int:
        lg = self._lgpio
        self._wait_ready()

        value = 0
        for _ in range(24):
            lg.gpio_write(self._chip, self._sck, 1)
            value = (value << 1) | lg.gpio_read(self._chip, self._dt)
            lg.gpio_write(self._chip, self._sck, 0)

        # extra pulses select channel A / gain for the *next* conversion
        for _ in range(self._gain_pulses):
            self._pulse()

        # 24-bit two's complement -> signed int
        if value & 0x800000:
            value -= 1 << 24
        return value

    def read_raw(self) -> float:
        with self._lock:
            return float(self._read_once())

    def reset(self) -> None:
        """Power the chip down and back up to clear a wedged conversion."""
        with self._lock:
            lg = self._lgpio
            lg.gpio_write(self._chip, self._sck, 1)
            time.sleep(0.08)          # >60 us holds it in power-down
            lg.gpio_write(self._chip, self._sck, 0)
            time.sleep(0.08)

    # -- health ------------------------------------------------------------
    def health(self) -> dict:
        info = {**super().health(), "dt_pin": self._dt, "sck_pin": self._sck,
                "gain": self._gain}
        try:
            stats = self.read_stats(samples=5)
            info["online"] = True
            info["raw"] = stats["raw"]
            info["raw_stdev"] = stats["raw_stdev"]
        except Exception as exc:
            info["online"] = False
            info["detail"] = str(exc)
        return info

    def close(self) -> None:
        try:
            self._lgpio.gpiochip_close(self._chip)
        except Exception:
            pass


class RemoteScale(BaseScale):
    """Reads raw counts pushed up by the Pi agent (cloud deployment).

    `read_raw()` returns the newest sample the agent sent. If the agent has not
    reported within `DEVICE_STALE_AFTER_S` the scale is "offline": either raise
    (so the caller can surface it) or fall back to a software scale, depending
    on ALLOW_SIM_FALLBACK.
    """

    kind = "remote"
    real_hardware = True

    def __init__(self) -> None:
        super().__init__()
        from app.devices import bridge

        self._bridge = bridge
        # only used when the device is offline and fallback is allowed
        self._fallback = SimulatedScale(detail="device offline fallback")

    @property
    def online(self) -> bool:
        return self._bridge.is_online()

    @property
    def fallback(self) -> SimulatedScale:
        return self._fallback

    @property
    def using_fallback(self) -> bool:
        return not self.online and settings.allow_sim_fallback

    def read_raw(self) -> float:
        raw = self._bridge.latest_raw()
        if raw is not None:
            return raw
        if settings.allow_sim_fallback:
            # software scale keeps grams == raw, and our offset/reference_unit
            # would distort that, so hand back a value that maps to its grams
            return self._fallback._grams * (self._reference_unit or 1.0) + self._offset
        raise RuntimeError(
            "Trolley device offline: no load-cell reading received. "
            "Check that pi_agent.py is running on the Pi."
        )

    def get_grams(self, samples: int = 8) -> float:
        # A remote reading is already median-filtered on the Pi, and repeated
        # reads return the same cached sample, so don't burn 8 x 5 ms here.
        return self.raw_to_grams(self.read_raw())

    def read_stats(self, samples: int = 15) -> dict:
        raw = self.read_raw()
        status = self._bridge.status()
        return {
            "raw": round(raw, 1),
            "grams": self.raw_to_grams(raw),
            "raw_stdev": 0.0,
            "raw_min": round(raw, 1),
            "raw_max": round(raw, 1),
            "samples": 1,
            "reading_age_s": status.get("reading_age_s"),
        }

    def tare(self, samples: int = 15) -> float:
        """Zero the basket using the agent's current reading."""
        if not self.online and settings.allow_sim_fallback:
            self._offset = self._fallback.tare(samples)
            return self._offset
        self._offset = self.read_raw()
        return self._offset

    def health(self) -> dict:
        status = self._bridge.status()
        online = self.online
        return {
            **super().health(),
            "online": online,
            "detail": (
                "" if online
                else ("device offline — using software fallback"
                      if settings.allow_sim_fallback
                      else "device offline — weight checks unavailable")
            ),
            "device": status,
            "using_fallback": self.using_fallback,
        }


# --- module-level singleton ------------------------------------------------
_scale: BaseScale | None = None
_init_error: str = ""


def _build_scale() -> BaseScale:
    global _init_error
    mode = settings.mode

    if mode == "cloud":
        return RemoteScale()

    if mode == "simulation":
        return SimulatedScale(detail="SIMULATION mode")

    # local: drive the HX711 on this machine
    try:
        return HX711Scale()
    except Exception as exc:
        _init_error = str(exc)
        # Loud, because a silent fallback here would quietly disable the
        # anti-theft weight check on a real trolley.
        print("=" * 72)
        print(f"[scale] HX711 UNAVAILABLE: {exc}")
        print("[scale] Falling back to the SOFTWARE scale. Weight verification")
        print("[scale] is NOT real until this is fixed. Run:")
        print("[scale]     python calibrate_scale.py diagnose")
        print("=" * 72)
        return SimulatedScale(detail=f"HX711 unavailable: {exc}")


def get_scale() -> BaseScale:
    global _scale
    if _scale is None:
        _scale = _build_scale()
        _apply_stored_calibration(_scale)
    return _scale


def _apply_stored_calibration(scale: BaseScale) -> None:
    """Load calibration saved by the settings UI / calibrate_scale.py."""
    if isinstance(scale, SimulatedScale):
        return
    try:
        from app.services import settings_store

        cal = settings_store.read_scale_calibration()
        scale.set_calibration(cal["reference_unit"], cal["offset"], cal["source"])
    except Exception as exc:  # DB not ready yet -> keep .env values
        print(f"[scale] using .env calibration ({exc})")


def reload_calibration() -> dict:
    """Re-read calibration from the store onto the live scale."""
    scale = get_scale()
    _apply_stored_calibration(scale)
    return scale.calibration


def scale_health() -> dict:
    scale = get_scale()
    info = scale.health()
    info["mode"] = settings.mode
    if _init_error:
        info["init_error"] = _init_error
    return info


def reset_scale() -> None:
    """Drop the singleton (used by tests and by mode changes)."""
    global _scale
    if _scale is not None:
        _scale.close()
    _scale = None
