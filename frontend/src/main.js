import 'preline';
import Alpine from "alpinejs";
import "./style.css";

document.addEventListener("alpine:init", () => {
  Alpine.data("app", () => ({
    // Tab state
    tabs: [
      { id: "dashboard", label: "Dashboard" },
      { id: "calibrator", label: "Calibrator" },
      { id: "farming", label: "Farming" },
      { id: "sequences", label: "Sequences" },
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

    // Bot state
    botState: "STOPPED",
    botRunning: false,
    currentGold: 0,
    currentElixir: 0,
    currentDarkElixir: 0,
    currentGems: 0,
    loopMode: "",
    wsBotStatus: null,
    activeSequenceId: null,

    // Calibrator state
    roiName: "",
    roiType: "tap",
    roiActive: false,
    roiStartX: 0,
    roiStartY: 0,
    roiCoords: "0, 0, 0, 0",
    savedRois: [],
    ocrResult: "",
    wizardMode: false,
    wizardStep: 0,
    wizardSequence: [
      { name: "btn_attack", label: "Tap the ATTACK button", type: "tap" },
      { name: "btn_find_match", label: "Tap FIND A MATCH button", type: "tap" },
      { name: "btn_next", label: "Tap the NEXT button", type: "tap" },
      { name: "gold_number", label: "Click on the GOLD number", type: "read" },
      { name: "elixir_number", label: "Click on the ELIXIR number", type: "read" },
      { name: "de_number", label: "Click on the DARK ELIXIR number", type: "read" },
      { name: "btn_return_home", label: "Tap RETURN HOME button", type: "tap" },
      { name: "btn_surrender", label: "Tap SURRENDER button", type: "tap" },
    ],

    // Analytics
    stats: { total_gold: 0, total_elixir: 0, total_de: 0, total_raids: 0 },
    attackHistory: [],
    searchEfficiency: { avg_skips: 0, max_skips: 0, min_skips: 0 },

    // Logs
    logLines: [],
    logFilter: "INFO",
    wsLogs: null,

    get filteredLogLines() {
      const lv = {DEBUG:0, INFO:1, WARNING:2, ERROR:3, CRITICAL:4};
      const min = lv[this.logFilter] ?? 1;
      return this.logLines.filter(line => (lv[line.level] ?? 1) >= min);
    },

    // Sequences
    sequences: [],
    selectedSequence: null,
    newStepType: "tap",
    newStepRoi: "",
    newStepDuration: "",
    newSeqName: "",
    roiSearch: "",

    // Farming config
    farmingConfig: {
      min_gold: 300000,
      min_elixir: 300000,
      min_dark_elixir: 500,
      max_searches: 30,
      strategy: "4finger",
    },

    // Builder tab
    upgradeQueue: [],
    newUpgradeName: "",
    newUpgradeLevel: 1,
    newUpgradeResource: "gold",
    upgradeStatus: { pending: 0, in_progress: 0, completed: 0, total: 0 },

    init() {
      this.startFpsCounter();
      this.loadFarmingConfig();
      this.loadAnalytics();
      this.connectLogs();

      // Re-send log filter to server when user changes tab
      this.$watch('logFilter', (filter) => {
        if (this.wsLogs && this.wsLogs.readyState === WebSocket.OPEN) {
          this.wsLogs.send(JSON.stringify({ filter }));
        }
      });
      this.loadRois();
      this.connectBotStatus();
      this.loadSequences();

      // Auto-capture screenshot when switching to calibrator tab
      this.$watch('activeTab', (tab) => {
        if (tab === 'calibrator' && this.adbStatus.connected) {
          this.captureScreenshot();
        }
      });
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
        if (this.adbStatus.connected && this.wsBotStatus && this.wsBotStatus.readyState === WebSocket.OPEN) {
          this.wsBotStatus.send(JSON.stringify({ command: "read_resources" }));
        }
        if (this.adbStatus.connected && this.activeTab === 'calibrator') {
          this.captureScreenshot();
        }
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

    async startBot() {
      const body = this.activeSequenceId ? JSON.stringify({ sequence_id: this.activeSequenceId }) : "{}";
      const res = await fetch("/api/v1/bot/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      const data = await res.json();
      this.botState = data.state;
      this.botRunning = data.running;
    },

    async stopBot() {
      const res = await fetch("/api/v1/bot/stop", { method: "POST" });
      const data = await res.json();
      this.botState = data.state;
      this.botRunning = data.running;
    },

    connectBotStatus() {
      if (this.wsBotStatus) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/status`);
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        this.botState = data.state;
        this.botRunning = data.running;
        this.currentGold = data.current_gold ?? 0;
        this.currentElixir = data.current_elixir ?? 0;
        this.currentDarkElixir = data.current_dark_elixir ?? 0;
        this.currentGems = data.current_gems ?? 0;
        this.loopMode = data.loop_mode ?? "";
      };
      ws.onclose = () => { this.wsBotStatus = null; };
      this.wsBotStatus = ws;
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
      // Clean up old event listeners before re-adding
      this._cleanupCalibratorListeners();

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

      await this.loadRois();
      this.drawSavedRois();
      this.setupRoiDrag();
      this.setupClickCalibrate();
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
        this._isDragging = false;
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
        this._isDragging = true;
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

      const onUp = (e) => {
        if (!dragging) return;
        dragging = false;
        // Use actual mouse event if available (mouseup), fallback to last known position (mouseleave)
        const useX = e && e.clientX !== undefined ? e.clientX : (window._lastMouseX || startX);
        const useY = e && e.clientY !== undefined ? e.clientY : (window._lastMouseY || startY);
        const pos = getPos({ clientX: useX, clientY: useY });
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
        body: JSON.stringify({ roi_name: this.roiName, roi_type: this.roiType, x_pos: x, y_pos: y, width: w, height: h }),
      });
      this.roiName = "";
      this.loadRois();
      if (this.wizardMode && this.wizardStep > 0) {
        this.wizardStep++;
        if (this.wizardStep > this.wizardSequence.length) {
          this.wizardMode = false;
          this.wizardStep = 0;
          alert('Wizard complete! All 8 ROIs saved.');
        } else {
          this.wizardAutoAdvance();
        }
      }
    },

    async loadRois() {
      const res = await fetch("/api/v1/roi");
      this.savedRois = await res.json();
    },

    async deleteRoi(id) {
      await fetch(`/api/v1/roi/${id}`, { method: "DELETE" });
      this.loadRois();
    },

    selectRoi(roi) {
      this.roiName = roi.roi_name;
      this.roiType = roi.roi_type || "tap";
      this.roiCoords = `${roi.x_pos}, ${roi.y_pos}, ${roi.width}, ${roi.height}`;
      this.roiActive = true;

      const canvas = document.getElementById("calibratorCanvas");
      const box = document.getElementById("roiBox");
      const rect = canvas.getBoundingClientRect();
      const scaleX = rect.width / canvas.width;
      const scaleY = rect.height / canvas.height;

      box.style.display = "block";
      box.style.left = (roi.x_pos * scaleX) + "px";
      box.style.top = (roi.y_pos * scaleY) + "px";
      box.style.width = (roi.width * scaleX) + "px";
      box.style.height = (roi.height * scaleY) + "px";
    },

    drawSavedRois() {
      const canvas = document.getElementById("calibratorCanvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");

      this.savedRois.forEach((roi) => {
        const isTap = roi.roi_type === "tap";
        const color = isTap ? "#22c55e" : "#a855f7";

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.setLineDash(isTap ? [] : [4, 4]);
        ctx.strokeRect(roi.x_pos, roi.y_pos, roi.width, roi.height);
        ctx.setLineDash([]);

        // Semi-transparent fill
        ctx.fillStyle = color + "20";
        ctx.fillRect(roi.x_pos, roi.y_pos, roi.width, roi.height);

        // Label
        const label = (isTap ? "T: " : "R: ") + roi.roi_name;
        const textWidth = ctx.measureText(label).width;
        ctx.fillStyle = color + "CC";
        ctx.fillRect(roi.x_pos, roi.y_pos - 18, textWidth + 10, 18);
        ctx.fillStyle = "#fff";
        ctx.font = "11px monospace";
        ctx.fillText(label, roi.x_pos + 5, roi.y_pos - 5);
      });
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

    async oneClickCalibrate(e) {
      const canvas = document.getElementById("calibratorCanvas");
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const x = Math.round((e.clientX - rect.left) * scaleX);
      const y = Math.round((e.clientY - rect.top) * scaleY);

      const isRead = this.roiType === "read";
      const boxW = isRead ? 120 : 100;
      const boxH = isRead ? 45 : 70;
      const halfW = Math.round(boxW / 2);
      const halfH = Math.round(boxH / 2);

      this.roiCoords = `${Math.max(0, x - halfW)}, ${Math.max(0, y - halfH)}, ${boxW}, ${boxH}`;
      this.roiActive = true;

      const box = document.getElementById("roiBox");
      const sX = rect.width / canvas.width;
      const sY = rect.height / canvas.height;
      box.style.display = "block";
      box.style.left = `${Math.max(0, (x - halfW) * sX)}px`;
      box.style.top = `${Math.max(0, (y - halfH) * sY)}px`;
      box.style.width = `${boxW * sX}px`;
      box.style.height = `${boxH * sY}px`;
    },

    loadPreset(name) {
      const roi = this.savedRois.find(r => r.roi_name === name);
      if (roi) {
        this.selectRoi(roi);
      } else {
        this.roiName = name;
        const defaults = {
          'btn_attack':        { x: 5, y: 560, w: 60, h: 130, t: 'tap' },
          'btn_find_match':    { x: 569, y: 444, w: 286, h: 93, t: 'tap' },
          'btn_next':          { x: 1069, y: 437, w: 203, h: 102, t: 'tap' },
          'gold_number':       { x: 22, y: 93, w: 150, h: 43, t: 'read' },
          'elixir_number':     { x: 22, y: 128, w: 150, h: 45, t: 'read' },
          'de_number':         { x: 22, y: 164, w: 150, h: 43, t: 'read' },
          'btn_return_home':   { x: 50, y: 580, w: 160, h: 100, t: 'tap' },
          'btn_surrender':     { x: 80, y: 660, w: 100, h: 50, t: 'tap' },
        };
        const d = defaults[name];
        if (d) {
          this.roiType = d.t;
          this.roiCoords = `${d.x}, ${d.y}, ${d.w}, ${d.h}`;
          this.roiActive = true;
        }
      }
    },

    onWizardToggle() {
      if (this.wizardMode) {
        this.wizardStep = 1;
        this.wizardAutoAdvance();
      } else {
        this.wizardStep = 0;
      }
    },

    wizardAutoAdvance() {
      if (!this.wizardMode || this.wizardStep < 1 || this.wizardStep > this.wizardSequence.length) return;
      const step = this.wizardSequence[this.wizardStep - 1];
      this.roiName = step.name;
      this.roiType = step.type;
    },

    _cleanupCalibratorListeners() {
      const canvas = document.getElementById("calibratorCanvas");
      if (!canvas) return;
      // Replace canvas element to remove all attached listeners
      const newCanvas = canvas.cloneNode(true);
      canvas.parentNode.replaceChild(newCanvas, canvas);
    },

    setupClickCalibrate() {
      const canvas = document.getElementById("calibratorCanvas");
      if (!canvas || canvas._clickCalibrateSet) return;
      canvas._clickCalibrateSet = true;

      canvas.addEventListener('mouseup', (e) => {
        if (!this._isDragging) {
          this.oneClickCalibrate(e);
        }
        this._isDragging = false;
      });
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

    async loadAnalytics() {
      try {
        const [summaryRes, historyRes, searchRes] = await Promise.all([
          fetch("/api/v1/analytics/summary"),
          fetch("/api/v1/analytics/history?limit=20"),
          fetch("/api/v1/analytics/search-efficiency"),
        ]);
        this.stats = await summaryRes.json();
        this.attackHistory = await historyRes.json();
        this.searchEfficiency = await searchRes.json();
        this.renderLootChart();
      } catch (e) {
        console.error("Analytics load failed:", e);
      }
    },

    async renderLootChart() {
      const ctx = document.getElementById("lootChart");
      if (!ctx) return;
      const res = await fetch("/api/v1/analytics/loot-rate?hours=24");
      const data = await res.json();

      if (this._lootChartInstance) this._lootChartInstance.destroy();

      // Check if Chart is available
      if (typeof Chart === "undefined") {
        // Dynamic import
        const { Chart: ChartJS, registerables } = await import("chart.js");
        ChartJS.register(...registerables);
      }

      this._lootChartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: data.map(d => d.hour?.slice(11, 16) || ""),
          datasets: [
            { label: "Gold", data: data.map(d => d.gold), borderColor: "#facc15", backgroundColor: "transparent", tension: 0.3 },
            { label: "Elixir", data: data.map(d => d.elixir), borderColor: "#f472b6", backgroundColor: "transparent", tension: 0.3 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#94a3b8", font: { size: 11 } } } },
          scales: {
            x: { ticks: { color: "#64748b", font: { size: 10 } }, grid: { color: "#1e293b" } },
            y: { ticks: { color: "#64748b", font: { size: 10 }, callback: v => this.formatNumber(v) }, grid: { color: "#1e293b" } },
          },
        },
      });
    },

    connectLogs() {
      if (this.wsLogs) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/logs`);

      ws.onopen = () => { ws.send(JSON.stringify({ filter: this.logFilter })); };
      ws.onmessage = (event) => {
        const entry = JSON.parse(event.data);
        entry._id = Date.now() + Math.random();
        this.logLines.push(entry);
        if (this.logLines.length > 200) this.logLines.shift();
        // Auto-scroll
        this.$nextTick(() => {
          const term = document.getElementById("logTerminal");
          if (term) term.scrollTop = term.scrollHeight;
        });
      };
      ws.onclose = () => { this.wsLogs = null; };
      this.wsLogs = ws;
    },

    clearLogs() {
      this.logLines = [];
    },

    async loadSequences() {
      const res = await fetch("/api/v1/sequences");
      this.sequences = await res.json();
      // Auto-select active sequence in dropdown
      const active = this.sequences.find(s => s.is_active);
      if (active) this.activeSequenceId = active.id;
      else if (this.sequences.length > 0) this.activeSequenceId = this.sequences[0].id;

      // Preserve current selection across reloads (e.g., after save)
      const currentId = this.selectedSequence?.id;
      if (this.sequences.length > 0) {
        if (currentId) {
          this.selectedSequence = this.sequences.find(s => s.id === currentId) || this.sequences[0];
        } else {
          this.selectedSequence = this.sequences[0];
        }
        this.selectedSequence.steps = this.selectedSequence.steps || [];
      }
    },

    loadSequence(seq) {
      this.selectedSequence = seq;
      // Load full sequence with steps
      fetch(`/api/v1/sequences`).then(r => r.json()).then(seqs => {
        const full = seqs.find(s => s.id === seq.id);
        if (full) this.selectedSequence = full;
      });
    },

    addStep() {
      if (!this.selectedSequence) return;
      const step = {
        step_order: this.selectedSequence.steps.length,
        step_type: this.newStepType,
        roi_name: this.newStepType === 'tap' ? this.newStepRoi : null,
        duration: this.newStepType === 'wait' ? parseFloat(this.newStepDuration) || 1 : null,
        config_json: null,
      };
      this.selectedSequence.steps.push(step);
      this.newStepRoi = "";
      this.newStepDuration = "";
    },

    removeStep(idx) {
      this.selectedSequence.steps.splice(idx, 1);
    },

    moveStep(idx, dir) {
      const steps = this.selectedSequence.steps;
      const newIdx = idx + dir;
      if (newIdx < 0 || newIdx >= steps.length) return;
      [steps[idx], steps[newIdx]] = [steps[newIdx], steps[idx]];
    },

    async saveSequence() {
      if (!this.selectedSequence) return;
      const steps = this.selectedSequence.steps.map((s, i) => ({
        step_order: i,
        step_type: s.step_type,
        roi_name: s.roi_name || null,
        duration: s.duration || null,
        config_json: s.config_json || null,
      }));
      await fetch(`/api/v1/sequences/${this.selectedSequence.id}/steps`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(steps),
      });
      this.loadSequences();
    },

    async createSequence() {
      const name = this.newSeqName.trim();
      if (!name) return;
      const res = await fetch("/api/v1/sequences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description: "" }),
      });
      if (res.ok) {
        this.newSeqName = "";
        await this.loadSequences();
        this.selectedSequence = this.sequences[this.sequences.length - 1];
        if (this.selectedSequence) this.selectedSequence.steps = [];
      } else if (res.status === 409) {
        alert("Sequence name already exists");
      }
    },

    async deleteSequence(id) {
      if (!confirm("Delete this sequence?")) return;
      await fetch(`/api/v1/sequences/${id}`, { method: "DELETE" });
      this.loadSequences();
      this.selectedSequence = null;
    },

    async activateSequence(id) {
      await fetch(`/api/v1/sequences/${id}/activate`, { method: "PUT" });
      this.loadSequences();
    },

    formatNumber(n) {
      if (n === null || n === undefined) return "0";
      if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
      if (n >= 1000) return (n / 1000).toFixed(1) + "K";
      return String(n);
    },

    formatTime(ts) {
      if (!ts) return "--";
      const d = new Date(ts);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    },

    // Tab watcher for analytics, logs, and calibrator
    watchTab(tab) {
      if (tab === 'analytics') this.loadAnalytics();
      if (tab === 'logs') this.connectLogs();
      if (tab === 'calibrator' && this.adbStatus.connected) this.captureScreenshot();
      if (tab === 'builder') {
        this.loadUpgradeQueue();
        this.loadUpgradeStatus();
      }
    },

    // --- Builder / Upgrade Queue ---
    async loadUpgradeQueue() {
      const res = await fetch("/api/v1/upgrade/queue");
      this.upgradeQueue = await res.json();
    },
    async loadUpgradeStatus() {
      const res = await fetch("/api/v1/upgrade/status");
      this.upgradeStatus = await res.json();
    },
    async addUpgradeItem() {
      if (!this.newUpgradeName.trim()) return;
      await fetch("/api/v1/upgrade/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: this.newUpgradeName.trim(),
          target_level: parseInt(this.newUpgradeLevel) || 1,
          resource_type: this.newUpgradeResource,
          upgrade_type: "building",
        }),
      });
      this.newUpgradeName = "";
      this.newUpgradeLevel = 1;
      this.newUpgradeResource = "gold";
      await this.loadUpgradeQueue();
      await this.loadUpgradeStatus();
    },
    async deleteUpgradeItem(id) {
      await fetch(`/api/v1/upgrade/queue/${id}`, { method: "DELETE" });
      await this.loadUpgradeQueue();
      await this.loadUpgradeStatus();
    },
    async moveUpgradeItem(id, dir) {
      const idx = this.upgradeQueue.findIndex(i => i.id === id);
      if (idx < 0) return;
      const target = idx + dir;
      if (target < 0 || target >= this.upgradeQueue.length) return;
      const a = this.upgradeQueue[idx], b = this.upgradeQueue[target];
      await fetch(`/api/v1/upgrade/queue/${a.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_order: b.priority_order }),
      });
      await fetch(`/api/v1/upgrade/queue/${b.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_order: a.priority_order }),
      });
      await this.loadUpgradeQueue();
    },
  }));
});

Alpine.start();
