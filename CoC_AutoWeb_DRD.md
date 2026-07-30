# Data Requirement Document (DRD)
## Clash of Clans Web-Based Automation Suite (CoC-AutoWeb)

| Parameter | Spesifikasi |
|---|---|
| **Nama Proyek** | CoC-AutoWeb Bot Suite |
| **Versi Dokumen** | v1.0.0 (Data Architecture) |
| **Penulis / Owner** | Backend Developer / Data Engineer |
| **Storage Engine** | SQLite 3 + Local File System |
| **Status Proyek** | Approved Architecture |
| **ORM Framework** | SQLModel / SQLAlchemy (FastAPI) |

---

## 1. Pendahuluan & Strategi Penyimpanan Data

Dokumen ini menetapkan spesifikasi kebutuhan data (Data Requirement Document) untuk platform **CoC-AutoWeb**. Meskipun sistem ini tidak memerlukan *database server* terpisah (seperti PostgreSQL atau MySQL), *persistence layer* tetap krusial untuk menyimpan koordinat hasil kalibrasi visual, konfigurasi bot, histori pertempuran untuk analitik, serta antrean *upgrade* bangunan.

Arsitektur penyimpanan menggunakan strategi hybrid:
* **Embedded Relational Database (SQLite):** Mengelola data terstruktur, status antrean, konfigurasi aplikasi, dan log pertempuran teragregasi.
* **Local Binary File Storage:** Menyimpan sampel gambar template OpenCV (`.png`) hasil pemotongan (*crop*) dari Canvas Web UI di direktori `/storage/templates/`.

> **Prinsip Desain Data:**
> Penyimpanan data dirancang *zero-configuration* (tanpa setup database eksternal), berbasis transaksi lokal yang aman (ACID-compliant), dan mendukung eksekusi query analitik ringan secara *real-time* untuk mensuplai data grafik pada Dashboard Web.

---

## 2. Diagram Konseptual & Relasi Antar Entitas (ERD)

Struktur entitas terhubung melalui kunci relasional sederhana untuk memastikan integritas data:

```
+-------------------------+          +-------------------------+
|     configs (KV Store)  |          |     roi_templates       |
+-------------------------+          +-------------------------+
| PK  key                 |          | PK  id                  |
|     value               |          |     roi_name            |
|     category            |          |     x_pos, y_pos        |
|     updated_at          |          |     width, height       |
+-------------------------+          |     image_path          |
                                     +-------------------------+

+-------------------------+          +-------------------------+
|     attack_logs         |          |     upgrade_queue       |
+-------------------------+          +-------------------------+
| PK  id                  |          | PK  id                  |
|     timestamp           |          |     building_name       |
|     gold_earned         |          |     target_level        |
|     elixir_earned       |          |     priority_order      |
|     dark_elixir_earned  |          |     status              |
|     trophies_change     |          |     started_at          |
|     search_count        |          |     completed_at        |
+-------------------------+          +-------------------------+
```

---

## 3. Spesifikasi Skema Tabel Database (SQLite)

### 3.1 Tabel: `configs`
Menyimpan parameter operasional bot, threshold farming, dan nilai humanisasi delay.

| Kolom | Tipe Data | Constraint | Deskripsi |
|---|---|---|---|
| `key` | VARCHAR(64) | `PRIMARY KEY` | Kunci unik konfigurasi (misal: `min_gold_threshold`). |
| `value` | TEXT | `NOT NULL` | Nilai variabel (dapat berupa string, integer, atau JSON string). |
| `category` | VARCHAR(32) | `NOT NULL` | Kategori setting: `FARMING`, `HUMANIZATION`, `SYSTEM`. |
| `updated_at` | DATETIME | `DEFAULT CURRENT_TIMESTAMP` | Waktu terakhir parameter diperbarui via Web UI. |

---

### 3.2 Tabel: `roi_templates`
Menyimpan koordinat area layar hasil kalibrasi dari Tab 2 Canvas UI untuk OpenCV template matching.

| Kolom | Tipe Data | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | `PRIMARY KEY AUTOINCREMENT` | ID unik templat. |
| `roi_name` | VARCHAR(64) | `UNIQUE, NOT NULL` | Nama elemen (misal: `btn_attack`, `gold_storage_box`). |
| `x_pos` | INTEGER | `NOT NULL` | Koordinat X sudut kiri atas pada acuan 1280x720. |
| `y_pos` | INTEGER | `NOT NULL` | Koordinat Y sudut kiri atas pada acuan 1280x720. |
| `width` | INTEGER | `NOT NULL` | Lebar area seleksi (piksel). |
| `height` | INTEGER | `NOT NULL` | Tinggi area seleksi (piksel). |
| `image_path` | VARCHAR(255) | `NULLABLE` | Path file lokal gambar templat (misal: `/storage/templates/btn_attack.png`). |

---

### 3.3 Tabel: `attack_logs`
Penyimpanan histori setiap pertempuran untuk menyuplai data grafik analitik di Tab 5.

| Kolom | Tipe Data | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | `PRIMARY KEY AUTOINCREMENT` | ID unik catatan pertempuran. |
| `timestamp` | DATETIME | `INDEX, DEFAULT CURRENT_TIMESTAMP` | Waktu eksekusi serangan selesai. |
| `gold_earned` | INTEGER | `DEFAULT 0` | Jumlah Gold yang berhasil dirampok. |
| `elixir_earned` | INTEGER | `DEFAULT 0` | Jumlah Elixir yang berhasil dirampok. |
| `dark_elixir_earned` | INTEGER | `DEFAULT 0` | Jumlah Dark Elixir yang berhasil dirampok. |
| `trophies_change` | INTEGER | `DEFAULT 0` | Perubahan tropi hasil pertempuran (+/-). |
| `search_count` | INTEGER | `DEFAULT 1` | Berapa kali tombol 'Next' ditekan sebelum menyerang. |

---

### 3.4 Tabel: `upgrade_queue`
Daftar antrean dan status pembangunan / upgrade bangunan untuk Tab 4.

| Kolom | Tipe Data | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | `PRIMARY KEY AUTOINCREMENT` | ID antrean item. |
| `building_name` | VARCHAR(64) | `NOT NULL` | Nama bangunan / hero (misal: `Archer Tower`, `Barbarian King`). |
| `target_level` | INTEGER | `NOT NULL` | Level sasaran setelah upgrade. |
| `priority_order` | INTEGER | `NOT NULL` | Urutan prioritas eksekusi antrean. |
| `status` | VARCHAR(20) | `DEFAULT 'PENDING'` | Status: `PENDING`, `IN_PROGRESS`, `COMPLETED`. |
| `started_at` | DATETIME | `NULLABLE` | Waktu saat builder mulai bekerja. |
| `completed_at` | DATETIME | `NULLABLE` | Estimasi / waktu sebenarnya upgrade selesai. |

---

## 4. Pola Akses Data & Query Analitik Utama

Berikut adalah query SQL standar yang dijalankan oleh backend FastAPI untuk mensuplai antarmuka Web UI:

### 4.1 Agregasi Pendapatan Loot per Jam (Tab 5 Analytics - Chart.js)
```sql
-- Mengambil total pendapatan loot per jam dalam 24 jam terakhir
SELECT 
    strftime('%Y-%m-%d %H:00:00', timestamp) AS hour_bucket,
    SUM(gold_earned) AS total_gold,
    SUM(elixir_earned) AS total_elixir,
    SUM(dark_elixir_earned) AS total_dark_elixir,
    COUNT(id) AS total_attacks
FROM attack_logs
WHERE timestamp >= datetime('now', '-24 hours')
GROUP BY hour_bucket
ORDER BY hour_bucket ASC;
```

### 4.2 Efisiensi Pencarian Base (Rata-rata Next per Raid)
```sql
-- Menghitung rata-rata skip/next sebelum menemukan target
SELECT 
    AVG(search_count) AS avg_skips,
    MAX(search_count) AS max_skips,
    MIN(search_count) AS min_skips
FROM attack_logs;
```

---

## 5. Manajemen Siklus Data & Persistence

* **Inisialisasi Otomatis (Auto-Migration):** Saat backend FastAPI pertama kali dijalankan, Pydantic/SQLModel akan memicu query `CREATE TABLE IF NOT EXISTS` untuk membuat database `coc_bot.db` secara otomatis jika belum ada.
* **Backup Config & Data:** Menyediakan endpoint API `GET /api/v1/system/backup` untuk mengunduh arsip `.zip` berisi file `coc_bot.db` dan direktori `/storage/templates/`.
* **Retention Policy (Pembersihan Log):** Log pertempuran yang berusia lebih dari 90 hari dapat dibersihkan secara otomatis via opsi *maintenance* di Web UI untuk menjaga ukuran file database tetap di bawah 20 MB.
