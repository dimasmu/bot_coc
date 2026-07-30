import Alpine from "alpinejs";
import "./style.css";

document.addEventListener("alpine:init", () => {
  Alpine.data("app", () => ({
    // Tab state
    tabs: [
      { id: "dashboard", label: "Dashboard" },
      { id: "calibrator", label: "Calibrator" },
      { id: "farming", label: "Farming" },
      { id: "builder", label: "Builder" },
      { id: "analytics", label: "Analytics" },
      { id: "logs", label: "Logs" },
    ],
    activeTab: "dashboard",

    // ADB connection state
    adbStatus: { connected: false, emulatorName: "", serial: "", screenSize: "" },
    adbHost: "127.0.0.1:5555",
    connecting: false,

    // Stream state
    wsScreen: null,
    streaming: false,
    fps: 0,
    _frameCount: 0,
    _fpsInterval: null,

    init() {
      this.startFpsCounter();
    },

    startFpsCounter() {
      this._fpsInterval = setInterval(() => {
        this.fps = this._frameCount;
        this._frameCount = 0;
      }, 1000);
    },

    async connectAdb() {
      this.connecting = true;
      const [host, portStr] = this.adbHost.split(":");
      const port = parseInt(portStr) || 5555;

      try {
        const res = await fetch("/api/v1/adb/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host, port }),
        });
        const data = await res.json();
        this.adbStatus = data.status;
      } catch (e) {
        console.error("ADB connect failed:", e);
      } finally {
        this.connecting = false;
      }
    },

    async disconnectAdb() {
      await fetch("/api/v1/adb/disconnect", { method: "POST" });
      this.adbStatus = { connected: false, emulatorName: "", serial: "", screenSize: "" };
    },

    startStream() {
      if (this.wsScreen) return;

      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/screen`);

      ws.binaryType = "blob";

      ws.onopen = () => {
        ws.send("start");
        this.streaming = true;
      };

      const canvas = document.getElementById("screenCanvas");
      const ctx = canvas.getContext("2d");

      ws.onmessage = async (event) => {
        if (event.data instanceof Blob) {
          this._frameCount++;
          try {
            const bitmap = await createImageBitmap(event.data);
            if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
              canvas.width = bitmap.width;
              canvas.height = bitmap.height;
            }
            ctx.drawImage(bitmap, 0, 0);
            bitmap.close();
          } catch (e) {
            // JSON error message, ignore binary parse failures
          }
        }
      };

      ws.onclose = () => {
        this.streaming = false;
        this.wsScreen = null;
      };

      ws.onerror = () => {
        this.streaming = false;
      };

      this.wsScreen = ws;
    },

    pauseStream() {
      if (this.wsScreen && this.wsScreen.readyState === WebSocket.OPEN) {
        this.wsScreen.send("pause");
        this.streaming = false;
      }
    },

    stopStream() {
      if (this.wsScreen) {
        if (this.wsScreen.readyState === WebSocket.OPEN) {
          this.wsScreen.send("stop");
        }
        this.wsScreen.close();
        this.wsScreen = null;
        this.streaming = false;
      }
    },
  }));
});

Alpine.start();
