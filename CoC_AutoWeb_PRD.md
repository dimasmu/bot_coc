# Product Requirement Document (PRD)
## Clash of Clans Web-Based Automation Suite (CoC-AutoWeb)

| Parameter | Spesifikasi |
|---|---|
| **Nama Proyek** | CoC-AutoWeb Bot Suite |
| **Versi Dokumen** | v1.0.0 (Final Architecture) |
| **Penulis / Owner** | Backend Developer / Engineering |
| **Target Stack** | Python 3.10+ (FastAPI) + Web Frontend + Pure-Python-ADB |
| **Status Proyek** | Proposed / Design Phase |
| **Interface Type** | Web Dashboard (Full PySide Equivalent) |

---

## 1. Ringkasan Eksekutif & Tujuan

**CoC-AutoWeb** adalah platform otomatisasi permainan *Clash of Clans (CoC)* berbasis *Computer Vision* dan *Android Debug Bridge (ADB)*. Sistem ini dikendalikan penuh melalui antarmuka web interaktif (Web Dashboard) yang kaya fitur—menyediakan fungsionalitas setingkat aplikasi desktop PySide6/Qt, namun dengan fleksibilitas akses via peramban web lokal.

Tujuan utama proyek ini adalah menyediakan sistem otomatisasi pencarian *loot* (Gold/Elixir/Dark Elixir), eksekusi serangan (*auto-farm attack*), manajemen *builder* (*auto-upgrade*), serta pemantauan real-time yang aman, terstruktur, dan memiliki toleransi kesalahan (*fault-tolerant*).

> **Catatan Etika & Keamanan (ToS Compliance):**
> Sistem ini dibuat untuk tujuan riset rekayasa perangkat lunak dan otomatisasi *computer vision*. Penggunaan pada akun utama berisiko terkena sanksi sesuai Terms of Service (ToS) Supercell. Fitur keamanan *humanization* wajib diaktifkan.

---

## 2. Arsitektur Sistem & Tech Stack

Sistem menggunakan arsitektur terpisah (*decoupled architecture*) antara Core Engine backend dengan Web Frontend menggunakan protokol WebSocket untuk komunikasi dua arah secara *real-time*.

```
┌─────────────────────────────────────────────────────────┐
│                    WEB FRONTEND (UI)                    │
│   Dashboard • Canvas ROI Calibrator • Charts • Logs    │
└───────────────────────────┬─────────────────────────────┘
                            │ WebSocket / REST API
┌───────────────────────────▼─────────────────────────────┐
│                 BACKEND ENGINE (FastAPI)                │
│   • FSM Controller           • Async Task Runner        │
│   • OpenCV Image Matching    • Tesseract OCR            │
└───────────────────────────┬─────────────────────────────┘
                            │ Pure-Python-ADB (TCP/IP)
┌───────────────────────────▼─────────────────────────────┐
│                 ANDROID EMULATOR (ADB)                  │
│    (LDPlayer / BlueStacks - 1280x720 DPI 240)           │
└─────────────────────────────────────────────────────────┘
```

| Komponen | Teknologi | Peran & Deskripsi |
|---|---|---|
| **Backend Server** | Python 3.10+ / FastAPI / Asyncio | Menjalankan FSM Engine, REST API untuk kontrol, dan WebSocket Server untuk *live stream* & log. |
| **Computer Vision** | OpenCV / NumPy / PyTesseract | Deteksi elemen UI, *template matching*, serta membaca angka *loot* & tropi via OCR. |
| **Emulator Interface** | Pure-Python-ADB / ADB CLI | Mengirim input tap/swipe, mengambil *frame screen capture* (10-15 FPS), dan *healthcheck* emulator. |
| **Frontend Framework** | HTML5 / TailwindCSS / Vanilla JS / Chart.js | Dashboard antarmuka modern dengan *multi-tab layout*, *canvas calibration tool*, dan *charting*. |
| **State & Persistence** | SQLite / JSON Config / Pydantic | Menyimpan konfigurasi bot, *coordinate templates*, dan histori statistik hasil *farming*. |

---

## 3. Spesifikasi Fitur Antarmuka Web UI (Multi-Tab Suite)

Frontend Web dirancang setara dengan aplikasi desktop kompleks (seperti PySide6) dengan membagi fungsi ke dalam 6 Tab Utama:

### **Tab 1: Dashboard Utama & Live Controller**
* **Live Screen Feed:** Streaming tampilan emulator via WebSocket / MJPEG canvas dengan latency < 150ms.
* **Global Controls:** Tombol `START`, `PAUSE`, dan `EMERGENCY STOP` (Hotkey: `Space` / `Esc`).
* **Status Indicator:** Display status FSM (`IDLE`, `TRAINING`, `SEARCHING`, `ATTACKING`, `UPGRADING`).
* **Quick Summary Cards:** Total Loot Terkumpul (Gold, Elixir, DE), Jumlah Raid Selesai, dan Runtime Bot.

### **Tab 2: Interactive ROI & Template Manager (Canvas Calibration)**
* **Visual Bounding Box Creator:** Pengguna dapat melakukan *click-and-drag* pada *screenshot* live untuk menentukan koordinat ROI (Region of Interest) tombol atau storage.
* **Template Crop & Save:** Memotong area tertentu dan menyimpannya sebagai sampel image template OpenCV langsung dari UI.
* **OCR Preview Test:** Fitur untuk menguji apakah Tesseract OCR dapat membaca angka Gold/Elixir pada area ROI yang dipilih secara *real-time*.

### **Tab 3: Strategi Farming & Logika Serangan**
* **Threshold Target Loot:** Input minimum Gold, Elixir, dan Dark Elixir sebelum bot memutuskan untuk menyerang.
* **Troop Preset Selector:** Pilihan komposisi pasukan (misal: *Barch*, *Baby Dragon*, *Balloons*).
* **Deployment Strategy:** Pilihan metode sebar pasukan (4-finger drop, perimeter line sweep, collector snipe).
* **Skip Filter:** Opsi melewati *base* dengan Town Hall tinggi atau *Active Inferno Towers*.

### **Tab 4: Builder & Auto-Upgrade Queue**
* **Builder Status Monitor:** Menampilkan jumlah builder yang sibuk beserta estimasi waktu selesai.
* **Upgrade Priority List:** Prioritas upgrade bangunan (misal: 1. Heroes -> 2. Resource -> 3. Defense).
* **Wall Dump Logic:** Opsi otomatis menghabiskan sisa Gold/Elixir untuk upgrade Tembok jika penyimpanan penuh dan ada 1 builder bebas.

### **Tab 5: Analytics & Performance Metrics**
* **Loot Rate Chart:** Grafik garis (*Line Chart*) pendapatan Gold/Elixir per jam menggunakan Chart.js.
* **Search Efficiency:** Histogram berapa kali rata-rata *Next* yang dilakukan sebelum menemukan base ideal.
* **Session History Table:** Tabel riwayat setiap serangan (Loot didapat, tropi (+/-), waktu serangan).

### **Tab 6: System Logs & ADB Health Monitor**
* **Real-Time Streaming Terminal:** Log aktivitas server yang mengalir via WebSocket.
* **Log Severity Filter:** Toggle filter tampilan (`INFO`, `WARNING`, `ERROR`, `DEBUG`).
* **ADB Connection Manager:** Dropdown pilihan IP/Port emulator, status koneksi ADB, dan tombol *Reconnect ADB*.

---

## 4. Spesifikasi Logika Otomatisasi (FSM Core)

Mesin utama bot digerakkan oleh *Finite State Machine (FSM)* untuk menjamin keandalan alur kerja:

| State FSM | Aksi Utama | Kondisi Transisi |
|---|---|---|
| `STATE_INIT` | Cek koneksi ADB, pastikan game CoC terbuka, sesuaikan resolusi. | Berhasil -> `STATE_MAIN_BASE` \| Gagal -> `STATE_RECOVERY` |
| `STATE_MAIN_BASE` | Panen kolektor, tutup pop-up/iklan, cek status builder & pasukan. | Pasukan Siap -> `STATE_SEARCHING` \| Pasukan Belum Siap -> `STATE_TRAINING` |
| `STATE_TRAINING` | Buka Barracks, klik Quick Train preset, tunggu timer. | Pasukan Penuh -> `STATE_SEARCHING` |
| `STATE_SEARCHING` | Klik Find Match, baca loot via OCR. | Loot >= Threshold -> `STATE_ATTACKING` \| Loot < Threshold -> Klik Next |
| `STATE_ATTACKING` | Jalankan pola sebar pasukan (*deployment script*), tunggu battle selesai. | Selesai Battle -> `STATE_RETURN_HOME` |
| `STATE_UPGRADING` | Cek Builder bebas, pilih bangunan dari priority queue, konfirmasi upgrade. | Selesai Upgrade -> `STATE_MAIN_BASE` |

---

## 5. Fitur Humanisasi & Anti-Deteksi

Untuk meminimalkan pola otomatisasi yang terdeteksi oleh algoritma anti-cheat:

* **Randomized Click Coordinates (Gaussian Offset):** Setiap ketukan tidak berada pada piksel yang sama, melainkan didistribusikan secara acak mengikuti kurva Gaussian pada area tombol (± 5-15 piksel).
* **Dynamic Delays:** Jeda waktu antar aksi dibuat bervariasi secara acak (misalnya jeda klik *Next* antara 1.2 detik hingga 3.5 detik).
* **Human Swipe Motion:** Gerakan *swipe* menyebar pasukan menggunakan algoritma Bezier Curve untuk meniru sapuan jari manusia (bukan garis lurus sempurna).
* **Break Interval (Rest Mode):** Bot secara periodik mengambil "istirahat" (*idle*) selama 10-20 menit setelah berjalan 2 jam penuh.

---

## 6. Non-Functional Requirements & Toleransi Kesalahan

* **Auto Game Recovery:** Jika game CoC mengalami *crash* atau muncul dialog *"Client and server out of sync"*, bot secara otomatis akan memuat ulang game dan kembali ke `STATE_INIT`.
* **Resolution Invariance:** Bot akan memaksa emulator berjalan pada resolusi standar (misal: `1280x720 DPI 240`) via command ADB saat inisialisasi agar koordinat tidak meleset.
* **Resource Usage:** Konsumsi CPU backend Python dijaga di bawah 15% pada CPU modern dengan mengoptimalkan pemrosesan frame OpenCV.
