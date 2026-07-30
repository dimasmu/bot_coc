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

    // Calibrator state
    roiName: "",
    roiActive: false,
    roiStartX: 0,
    roiStartY: 0,
    roiCoords: "0, 0, 0, 0",
    savedRois: [],
    ocrResult: "",

    // Farming config
    farmingConfig: {
      min_gold: 300000,
      min_elixir: 300000,
      min_dark_elixir: 500,
      max_searches: 30,
      strategy: "4finger",
    },

    init() {
      this.startFpsCounter();
      this.loadFarmingConfig();
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

    async captureScreenshot() {
      const res = await fetch("/api/v1/screenshot");
      const data = await res.json();
      if (!data.png_base64) return;

      const bytes = new Uint8Array(data.png_base64.match(/.{1,2}/g).map(b => parseInt(b, 16)));
      const blob = new Blob([bytes], { type: "image/png" });
      const bitmap = await createImageBitmap(blob);

      const canvas = document.getElementById("calibratorCanvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();

      this.setupRoiDrag();
      this.loadRois();
    },

    setupRoiDrag() {
      const canvas = document.getElementById("calibratorCanvas");
      const box = document.getElementById("roiBox");
      const container = document.getElementById("calibratorContainer");
      let dragging = false;
      let startX, startY;

      const getPos = (e) => {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        return {
          x: Math.round((clientX - rect.left) * scaleX),
          y: Math.round((clientY - rect.top) * scaleY),
        };
      };

      const onDown = (e) => {
        e.preventDefault();
        dragging = true;
        const pos = getPos(e);
        startX = pos.x;
        startY = pos.y;
        this.roiStartX = startX;
        this.roiStartY = startY;
        box.style.display = "block";
        this.roiActive = false;
      };

      const onMove = (e) => {
        if (!dragging) return;
        const pos = getPos(e);
        const x = Math.min(startX, pos.x);
        const y = Math.min(startY, pos.y);
        const w = Math.abs(pos.x - startX);
        const h = Math.abs(pos.y - startY);

        const rect = canvas.getBoundingClientRect();
        const scaleX = rect.width / canvas.width;
        const scaleY = rect.height / canvas.height;

        box.style.left = (x * scaleX) + "px";
        box.style.top = (y * scaleY) + "px";
        box.style.width = (w * scaleX) + "px";
        box.style.height = (h * scaleY) + "px";
      };

      const onUp = () => {
        if (!dragging) return;
        dragging = false;
        const pos = getPos({ clientX: window._lastMouseX || startX, clientY: window._lastMouseY || startY });
        const x = Math.min(this.roiStartX, pos.x || this.roiStartX);
        const y = Math.min(this.roiStartY, pos.y || this.roiStartY);
        const w = Math.abs((pos.x || this.roiStartX) - this.roiStartX);
        const h = Math.abs((pos.y || this.roiStartY) - this.roiStartY);
        if (w > 5 && h > 5) {
          this.roiCoords = `${x}, ${y}, ${w}, ${h}`;
          this.roiActive = true;
        } else {
          box.style.display = "none";
        }
      };

      canvas.onmousedown = onDown;
      canvas.onmousemove = (e) => { window._lastMouseX = e.clientX; window._lastMouseY = e.clientY; onMove(e); };
      canvas.onmouseup = onUp;
      canvas.onmouseleave = onUp;
      canvas.ontouchstart = onDown;
      canvas.ontouchmove = onMove;
      canvas.ontouchend = onUp;
    },

    async saveRoi() {
      if (!this.roiActive || !this.roiName) {
        alert("Drag on the screenshot and enter an ROI name");
        return;
      }
      const [x, y, w, h] = this.roiCoords.split(",").map(s => parseInt(s.trim()));
      await fetch("/api/v1/roi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roi_name: this.roiName, x_pos: x, y_pos: y, width: w, height: h }),
      });
      this.roiName = "";
      this.loadRois();
    },

    async loadRois() {
      const res = await fetch("/api/v1/roi");
      this.savedRois = await res.json();
    },

    async deleteRoi(id) {
      await fetch(`/api/v1/roi/${id}`, { method: "DELETE" });
      this.loadRois();
    },

    async testOcr() {
      const [x, y, w, h] = this.roiCoords.split(",").map(s => parseInt(s.trim()));
      const res = await fetch("/api/v1/ocr/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x, y, width: w, height: h }),
      });
      const data = await res.json();
      this.ocrResult = data.text || "(no text)";
    },

    clearRoi() {
      this.roiActive = false;
      this.roiCoords = "0, 0, 0, 0";
      document.getElementById("roiBox").style.display = "none";
    },

    async loadFarmingConfig() {
      try {
        const res = await fetch("/api/v1/config?category=FARMING");
        const items = await res.json();
        for (const item of items) {
          if (item.key === "min_gold_threshold") this.farmingConfig.min_gold = parseInt(item.value);
          else if (item.key === "min_elixir_threshold") this.farmingConfig.min_elixir = parseInt(item.value);
          else if (item.key === "min_dark_elixir_threshold") this.farmingConfig.min_dark_elixir = parseInt(item.value);
          else if (item.key === "max_searches") this.farmingConfig.max_searches = parseInt(item.value);
          else if (item.key === "strategy") this.farmingConfig.strategy = item.value;
        }
      } catch (e) {
        console.error("Failed to load farming config:", e);
      }
    },

    async saveFarmingConfig() {
      const items = [
        { key: "min_gold_threshold", value: String(this.farmingConfig.min_gold), category: "FARMING" },
        { key: "min_elixir_threshold", value: String(this.farmingConfig.min_elixir), category: "FARMING" },
        { key: "min_dark_elixir_threshold", value: String(this.farmingConfig.min_dark_elixir), category: "FARMING" },
        { key: "max_searches", value: String(this.farmingConfig.max_searches), category: "FARMING" },
        { key: "strategy", value: this.farmingConfig.strategy, category: "FARMING" },
      ];
      for (const item of items) {
        await fetch(`/api/v1/config/${item.key}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item),
        });
      }
    },
  }));
});

Alpine.start();
