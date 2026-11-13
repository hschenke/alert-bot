# Alert-Bot

Ein autonomer, KI-gestützter **24/7 Smart-Alert-Bot**, dem du in normaler Sprache sagen kannst, was er für dich im Blick behalten soll – und der dich sofort **per E-Mail benachrichtigt**, sobald deine Bedingung eintritt oder ein neues passendes Angebot online geht.

Aufgebaut auf derselben bewährten **0-€-Cloud-Architektur** wie `news-aggregator-bot` und `research-bot` (**Streamlit Community Cloud + GitHub Actions + Turso LibSQL + Google Gemini + Open-Meteo + Resend**) mit striktem Fokus auf **Clean Code, Anti-Spam-Intelligenz und Security**.

---

## ✨ Typische Anwendungsfälle

- 🌡️ **Wetter- & Temperatur-Wächter** *(ohne API-Key über Open-Meteo + KI-Fallback)*:
  > *„Beobachte die Temperatur in Freiburg und teile mir mit, sobald sie 22 °C erreicht oder unter 0 °C fällt.“*
- 🏠 **Mietwohnungs- & Angebots-Radar** *(Google Search Grounding + Duplikat-Gedächtnis)*:
  > *„Schaue nach neuen 3-Zimmer-Mietwohnungen in Berlin-Mahlsdorf bis 1.300 € Warmmiete und sende eine E-Mail, sobald ein neues Inserat online ist.“*
- 💰 **Preis-, Aktien- & Finanz-Schwellenwerte**:
  > *„Überwache den Preis auf dieser Produktseite (oder den Goldpreis in €) und melde dich, sobald er unter Zielwert X fällt.“*
- 📄 **Website-Änderungen & freie Termine**:
  > *„Prüfe alle 15 Minuten eine Behörden-, Arzt- oder Shop-Seite auf freie Termine / Lagerbestand – ohne Fehlalarme durch Cookie-Banner.“*
- 🩺 **Uptime-, HTTP- & JSON-API-Monitoring**:
  > *„Alarmiere mich, sobald ein Webservice nicht mehr mit HTTP 200 antwortet oder ein JSON-Feld seinen Sollwert verlässt.“*

---

## 🏗️ Architektur (0 € / Monat, 24/7 autark)

```text
┌──────────────────────────────────────────────────────────────────────┐
│  1. Streamlit Web-App (Desktop & Smartphone)                         │
│     • Freitext-KI-Assistent („Beobachte ...“) + Detail-Konfigurator  │
│     • Speichert Regeln in Turso Cloud (lokaler Fallback: SQLite WAL) │
│     • Sofort-Check auf Knopfdruck + Live-Prüfprotokolle              │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  2. Turso Cloud DB (LibSQL via HTTPS Hrana v2) / Lokal: SQLite WAL   │
│     • Atomares Rule-Claiming (`locked_until` verhindert Doppelmails) │
│     • Seen-Items-Gedächtnis (meldet nur WIRKLICH neue Wohnungen)     │
│     • Anti-Spam State-Machine (State-Change, Cooldown, Einmal-Alarm) │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  3. GitHub Actions Cloud-Watcher (`check_alerts.yaml`)               │
│     • Standard: Automatischer 15-Min.-Cron (Public Repo = gratis)    │
│     • Optional: 1–5-Min.-Präzision kostenlos via cron-job.org REST   │
│     • Sendet formatierte HTML-Mail mit signiertem Smartphone-Link    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🛡️ Clean Code & Security Highlights

- **Strikte Schichtenarchitektur (SOLID)**: Saubere Trennung in `domain`, `config`, `security`, `storage`, `detectors`, `notifications`, `worker` und `ui`.
- **Zero SQL Duplication**: Ein einziges `SqlAlertRepository` bedient über das `SqlExecutor`-Protokoll sowohl lokales SQLite (WAL) als auch Turso Cloud mit 100 % parametrisierten `?`-Queries.
- **SSRF- & DNS-Rebinding-Schutz (`ssrf_guard.py` + `SafeHttpFetcher`)**: Validiert jede Ziel-URL und **jeden einzelnen HTTP-Redirect-Hop** gegen Loopback (`127.0.0.1`, `::1`), private RFC-1918-Netze und Cloud-Metadaten (`169.254.169.254`) bei striktem 2-MB-Streaming-Limit.
- **XSS-sichere HTML-Mails & Signierte Read-Only-Links**: Alle externen Texte/Links werden strikt escaped bzw. auf `http`/`https` geprüft; E-Mails enthalten auf Wunsch ein HMAC-signiertes Lese-Token für den 1-Klick-Aufruf auf dem Smartphone.
- **Erweiterbare Kanal-Architektur (`NotificationRouter`)**: Startet bewusst schlank mit **E-Mail (Resend)**; Adapter für **ntfy.sh Push**, **Telegram Bot** und **Webhooks (Discord/Slack/Home Assistant)** sind architektonisch bereits vorbereitet.

---

## 🚀 Lokale Einrichtung & Start

```powershell
# 1. Virtuelle Umgebung erstellen & aktivieren (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Abhängigkeiten installieren
python -m pip install -r requirements.txt

# 3. Umgebungsvariablen anlegen
Copy-Item .env.example .env
# -> GEMINI_API_KEY und optional RESEND_API_KEY / EMAIL_TO in .env eintragen
#    (Wetter-, Uptime- & Seiten-Checks funktionieren sogar komplett ohne API-Key!)

# 4. Web-App starten
streamlit run streamlit_app.py
```

### Qualitätssicherung (Linter, Security-Check & Tests)
```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pytest.exe -v
```

---

## ☁️ Kostenloses Cloud-Deployment (0 € / Monat)

### 1. Turso Cloud-Datenbank erstellen
1. Auf [turso.tech](https://turso.tech) neue Datenbank anlegen (z. B. `alert-bot`).
2. `TURSO_URL` (`libsql://...`) und `TURSO_KEY` kopieren.

### 2. GitHub Repository Secrets hinterlegen
Unter **GitHub -> Settings -> Secrets and variables -> Actions** eintragen:
- `GEMINI_API_KEY`
- `TURSO_URL` & `TURSO_KEY`
- `RESEND_API_KEY`, `EMAIL_TO` (und optional `EMAIL_FROM`)
- `APP_PASSWORD` & `STREAMLIT_APP_URL`

### 3. Streamlit Community Cloud verbinden
1. Auf [share.streamlit.io](https://share.streamlit.io) neue App aus `hschenke/alert-bot` (`main`, `streamlit_app.py`) deployen.
2. Unter **App Settings -> Secrets** die Werte aus `.streamlit/secrets.toml.example` eintragen.

### 4. Optional: Punktgenauer 1–5-Minuten-Takt via cron-job.org (0 €)
Falls einzelne Wächter im 1–5-Minuten-Intervall ohne den üblichen GitHub-Cron-Versatz laufen sollen:
- Kostenlosen Account auf [cron-job.org](https://cron-job.org) anlegen.
- HTTP `POST` auf `https://api.github.com/repos/hschenke/alert-bot/actions/workflows/check_alerts.yaml/dispatches` mit Fine-Grained GitHub Token (`Actions: Read and write`) und Body `{"ref":"main","inputs":{"rule_id":"","force":"false"}}` einrichten.
