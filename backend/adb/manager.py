"""ADB connection lifecycle manager with health checks and screencap."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.auth.keygen import keygen

from backend.adb.emulators import EmulatorAdapter, get_emulator_adapters
from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AdbStatus:
    connected: bool = False
    emulator_name: str = ""
    serial: str = ""
    screen_size: str = ""


class AdbManager:
    """Manages the lifecycle of an ADB connection to an Android emulator."""

    def __init__(self):
        self._device: AdbDeviceTcp | None = None
        self._signer: PythonRSASigner | None = None
        self._adapter: EmulatorAdapter | None = None
        self.status = AdbStatus()
        self._lock = asyncio.Lock()

    def _ensure_keys(self, key_path: str = "storage/adbkey") -> PythonRSASigner:
        """Load or generate ADB RSA key pair."""
        key_file = Path(key_path)
        pub_file = Path(str(key_path) + ".pub")
        if not key_file.exists() or not pub_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            keygen(str(key_file))
            logger.info("Generated new ADB keypair at %s", key_file)

        with open(key_file) as f:
            priv = f.read()
        with open(pub_file) as f:
            pub = f.read()
        return PythonRSASigner(pub, priv)

    async def _run_shell(self, cmd: str, decode: bool = True) -> str | bytes:
        """Run a shell command on the device via the executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._device.shell(cmd, decode=decode),
        )

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> bool:
        """Connect to an emulator. Tries auto-detection, falls back to manual.

        Returns True if connected successfully.
        """
        async with self._lock:
            await self._disconnect()

            self._signer = self._ensure_keys()

            adapters = get_emulator_adapters(
                host_override=host,
                port_override=port,
            )

            for adapter in adapters:
                if host is None and not adapter.detect_running():
                    logger.debug("Skipping %s: not detected", adapter.name)
                    continue

                logger.info("Trying %s at %s:%d ...", adapter.name, adapter.adb_host, adapter.adb_port)
                try:
                    device = AdbDeviceTcp(
                        adapter.adb_host,
                        adapter.adb_port,
                        default_transport_timeout_s=9.0,
                    )
                    device.connect(rsa_keys=[self._signer], auth_timeout_s=0.1)
                    self._device = device
                    self._adapter = adapter

                    resolution = await self._run_shell("wm size")
                    self.status = AdbStatus(
                        connected=True,
                        emulator_name=adapter.name,
                        serial=adapter.get_device_serial(),
                        screen_size=resolution.strip(),
                    )
                    logger.info("Connected to %s (%s)", adapter.name, resolution.strip())
                    return True

                except Exception as e:
                    logger.warning("Failed to connect to %s: %s", adapter.name, e)
                    try:
                        device.close()
                    except Exception:
                        pass
                    self._device = None

            self.status = AdbStatus()
            return False

    async def _disconnect(self) -> None:
        """Close the ADB connection (no lock -- for internal use)."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
            self._adapter = None
            self.status = AdbStatus()

    async def disconnect(self) -> None:
        """Close the ADB connection."""
        async with self._lock:
            await self._disconnect()

    async def screencap(self) -> bytes | None:
        """Capture the device screen as PNG bytes.

        Returns None if the capture fails.
        """
        async with self._lock:
            if self._device is None:
                return None

            loop = asyncio.get_running_loop()
            try:
                return await loop.run_in_executor(
                    None,
                    lambda: self._device.shell("screencap -p", decode=False),
                )
            except Exception as e:
                logger.error("screencap failed: %s", e)
                return None

    async def set_resolution(self) -> bool:
        """Enforce target resolution on the device."""
        async with self._lock:
            if self._device is None:
                return False
            try:
                await self._run_shell(f"wm size {settings.screen_width}x{settings.screen_height}")
                await self._run_shell(f"wm density {settings.screen_dpi}")
                return True
            except Exception as e:
                logger.error("Failed to set resolution: %s", e)
                return False

    async def tap(self, x: int, y: int) -> bool:
        """Tap at coordinates."""
        async with self._lock:
            if self._device is None:
                return False
            try:
                await self._run_shell(f"input tap {x} {y}")
                return True
            except Exception as e:
                logger.error("tap failed: %s", e)
                return False

    async def health_check(self) -> bool:
        """Check if the ADB connection is still alive."""
        async with self._lock:
            if self._device is None:
                return False
            try:
                await self._run_shell("echo ok")
                return True
            except Exception:
                return False

    @property
    def is_connected(self) -> bool:
        return self.status.connected


adb_manager = AdbManager()
