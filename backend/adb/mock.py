"""Mock ADB adapter that replays pre-captured screenshots for testing."""

from pathlib import Path


class MockAdbManager:
    """Replays PNG screenshots from disk instead of connecting to a real device."""

    def __init__(self, screenshot_dir: str = "tests/fixtures/screenshots"):
        self.dir = Path(screenshot_dir)
        self.status = type(
            "Status",
            (),
            {
                "connected": True,
                "emulator_name": "Mock emulator",
                "serial": "mock:5555",
                "screen_size": "Physical size: 1280x720",
            },
        )()
        self._current_file = None

    @property
    def is_connected(self) -> bool:
        return True

    async def connect(self, **kwargs):
        return True

    async def disconnect(self):
        pass

    async def screencap(self) -> bytes | None:
        """Return a screenshot from the fixtures directory."""
        if self._current_file:
            path = self.dir / self._current_file
            if path.exists():
                return path.read_bytes()
        # Default: main base
        path = self.dir / "main_base.png"
        if path.exists():
            return path.read_bytes()
        return None

    async def set_resolution(self) -> bool:
        return True

    def set_screen(self, filename: str):
        """Set which screenshot to return on next screencap()."""
        self._current_file = filename

    async def tap(self, x: int, y: int) -> bool:
        return True

    async def health_check(self) -> bool:
        return True
