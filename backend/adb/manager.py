"""ADB connection lifecycle manager using system ADB binary (subprocess)."""

import asyncio
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

from backend.adb.emulators import get_emulator_adapters
from backend.config import settings

logger = logging.getLogger(__name__)

# Possible ADB binary locations (checked in order)
_ADB_PATHS = [
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
]

# Also check PATH
_system_adb = shutil.which("adb")
if _system_adb:
    _ADB_PATHS.append(_system_adb)


def _find_adb() -> str:
    """Find the ADB binary. Returns the first found path."""
    for path in _ADB_PATHS:
        if os.path.isfile(path):
            logger.debug("Using ADB: %s", path)
            return path
    raise RuntimeError(
        "ADB binary not found. Install Android SDK Platform Tools or BlueStacks."
    )


ADB_BINARY = _find_adb()


def _adb(args: list[str]) -> str:
    """Run an ADB command synchronously (used in thread executor)."""
    cmd = [ADB_BINARY] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            raise RuntimeError(stderr)
    return result.stdout.strip()


@dataclass
class AdbStatus:
    connected: bool = False
    emulator_name: str = ""
    serial: str = ""
    screen_size: str = ""


class AdbManager:
    """Manages ADB connection via system binary (subprocess)."""

    def __init__(self):
        self._serial: str | None = None
        self._adapter_name: str = ""
        self.status = AdbStatus()
        self._lock = asyncio.Lock()

    async def _run_adb(self, *args: str) -> str:
        """Run an ADB command in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _adb, list(args))

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> bool:
        """Connect to an emulator. Tries auto-detection, falls back to manual."""
        async with self._lock:
            await self._disconnect_unsafe()

            adapters = get_emulator_adapters(
                host_override=host,
                port_override=port,
            )

            for adapter in adapters:
                if host is None and not adapter.detect_running():
                    logger.debug("Skipping %s: not detected", adapter.name)
                    continue

                serial = adapter.get_device_serial()
                logger.info("Connecting to %s ...", serial)

                try:
                    output = await self._run_adb("connect", serial)
                    logger.info("Connect output: %s", output.strip())

                    # Verify connection
                    await self._run_adb("-s", serial, "shell", "echo", "ok")
                    self._serial = serial
                    self._adapter_name = adapter.name

                    # Check resolution
                    res = await self._run_adb("-s", serial, "shell", "wm", "size")
                    logger.info("Resolution: %s", res.strip())

                    # Enforce target resolution
                    await self._run_adb(
                        "-s", serial, "shell", "wm", "size",
                        f"{settings.screen_width}x{settings.screen_height}",
                    )
                    await self._run_adb(
                        "-s", serial, "shell", "wm", "density",
                        str(settings.screen_dpi),
                    )

                    self.status = AdbStatus(
                        connected=True,
                        emulator_name=adapter.name,
                        serial=serial,
                        screen_size=f"{settings.screen_width}x{settings.screen_height}",
                    )
                    logger.info("Connected to %s", adapter.name)
                    return True

                except Exception as e:
                    logger.warning("Failed to connect: %s", e)

            self.status = AdbStatus()
            return False

    async def _disconnect_unsafe(self) -> None:
        """Disconnect without acquiring lock (caller must hold lock)."""
        if self._serial:
            try:
                await self._run_adb("disconnect", self._serial)
            except Exception:
                pass
        self._serial = None
        self._adapter_name = ""
        self.status = AdbStatus()

    async def disconnect(self) -> None:
        """Close the ADB connection."""
        async with self._lock:
            await self._disconnect_unsafe()

    async def screencap(self) -> bytes | None:
        """Capture the device screen as PNG bytes."""
        if self._serial is None:
            return None
        try:
            loop = asyncio.get_running_loop()
            cmd = [ADB_BINARY, "-s", self._serial, "exec-out", "screencap", "-p"]
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, timeout=10),
            )
            if result.returncode != 0:
                logger.error("screencap failed")
                return None
            return result.stdout
        except Exception as e:
            logger.error("screencap failed: %s", e)
            return None

    async def set_resolution(self) -> bool:
        """Enforce target resolution on the device."""
        if self._serial is None:
            return False
        try:
            await self._run_adb(
                "-s", self._serial, "shell", "wm", "size",
                f"{settings.screen_width}x{settings.screen_height}",
            )
            await self._run_adb(
                "-s", self._serial, "shell", "wm", "density",
                str(settings.screen_dpi),
            )
            return True
        except Exception as e:
            logger.error("Failed to set resolution: %s", e)
            return False

    async def tap(self, x: int, y: int) -> bool:
        """Tap at coordinates via ADB input tap."""
        if self._serial is None:
            return False
        try:
            await self._run_adb(
                "-s", self._serial, "shell", "input", "tap",
                str(x), str(y),
            )
            return True
        except Exception as e:
            logger.error("tap failed: %s", e)
            return False

    async def health_check(self) -> bool:
        """Check if ADB connection is alive."""
        if self._serial is None:
            return False
        try:
            await self._run_adb("-s", self._serial, "shell", "echo", "ok")
            return True
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        return self.status.connected


adb_manager = AdbManager()
