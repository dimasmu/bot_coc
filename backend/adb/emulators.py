"""Emulator-specific ADB adapters with auto-detection."""

from abc import ABC, abstractmethod
import subprocess
import sys


class EmulatorAdapter(ABC):
    """Base class for emulator ADB connections."""

    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    name: str = "generic"

    @abstractmethod
    def detect_running(self) -> bool:
        """Return True if this emulator is running."""
        ...

    def get_device_serial(self) -> str:
        """Return the ADB device serial string."""
        return f"{self.adb_host}:{self.adb_port}"


class BlueStacks5Adapter(EmulatorAdapter):
    """BlueStacks 5 running on default port 5555."""

    adb_port = 5555
    name = "BlueStacks 5"

    def detect_running(self) -> bool:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq HD-Player.exe"],
                    capture_output=True,
                    text=True,
                )
                return "HD-Player.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class BlueStacks4Adapter(EmulatorAdapter):
    """BlueStacks 4 -- same process name, different config layout."""

    adb_port = 5555
    name = "BlueStacks 4"

    def detect_running(self) -> bool:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq HD-Player.exe"],
                    capture_output=True,
                    text=True,
                )
                return "HD-Player.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class LDPlayerAdapter(EmulatorAdapter):
    """LDPlayer emulator."""

    adb_port = 5555
    name = "LDPlayer"

    def detect_running(self) -> bool:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq dnplayer.exe"],
                    capture_output=True,
                    text=True,
                )
                return "dnplayer.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class GenericTcpAdapter(EmulatorAdapter):
    """Fallback: user-provided IP:port."""

    name = "Generic TCP"

    def detect_running(self) -> bool:
        return False


def get_emulator_adapters(
    host_override: str | None = None,
    port_override: int | None = None,
) -> list[EmulatorAdapter]:
    """Return adapters in detection priority order.

    If host/port overrides are given, the GenericTcpAdapter uses them
    and is moved to first position.
    """
    adapters: list[EmulatorAdapter] = [
        BlueStacks5Adapter(),
        BlueStacks4Adapter(),
        LDPlayerAdapter(),
        GenericTcpAdapter(),
    ]

    if host_override is not None:
        generic = adapters[-1]
        generic.adb_host = host_override
        if port_override is not None:
            generic.adb_port = port_override
        adapters.insert(0, adapters.pop())

    return adapters
