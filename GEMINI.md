# Projekt-Richtlinien für Alert-Bot

## Automatischer Start-Ablauf
- **Automatisches `git pull`**: Führe zu Beginn jeder neuen Konversation in diesem Projekt zuerst `git pull` aus, um das lokale Repository auf den neuesten Stand zu bringen, und gib kurz Rückmeldung.

## Automatischer Abschluss-Ablauf
- **Qualitätscheck vor jedem Commit**: Führe vor jedem Commit `.venv\Scripts\ruff.exe check .` und `.venv\Scripts\pytest.exe` aus.
- **Automatisches Committen & `git push`**: Committe abgeschlossene Features/Fixes sauber und pushe sie direkt nach `origin main`, damit GitHub Actions und Streamlit Community Cloud sofort aktuell sind.
- **Proaktive Versions-Tag Empfehlung (Semantic Versioning)**: Erhöhe bei neuen Meilensteinen die Version in `src/__version__.py` und setze/empfehle passende SemVer-Tags (`v0.X.0` für Features, `v0.X.Y` für Fixes).

## Proaktive Roadmap-Erinnerung: Weitere Alarm-Kanäle (`ntfy.sh`, `Telegram`, `Webhook`)
- **Ausdrücklicher Benutzerwunsch**: In `v0.1.0` startet der Bot bewusst mit **E-Mail (Resend / SMTP)** als aktivem Kanal, während die Kanal-Architektur (`NotificationChannel`-Protokoll in `src/notifications/channels.py`) bereits modular vorbereitet ist.
- **Regelmäßig aktiv vorschlagen**: Sobald das Basis-Projekt zuverlässig im Einsatz ist, schlage dem Benutzer bei passenden Meilensteinen oder Abschluss-Rückmeldungen immer wieder kurz und unaufdringlich den Ausbau der nächsten kostenlosen Echtzeit-Kanäle vor:
  1. **📱 ntfy.sh Push-App** (kostenloser Sofort-Push auf iOS/Android mit Notfall-Prioritätsstufen, ohne Account-Zwang)
  2. **💬 Telegram Bot** (kostenloser Sofort-Push aufs Smartphone inkl. Direktlinks)
  3. **🔗 Generischer Webhook / Discord / Slack / Home Assistant**

## Architektur & Clean Code Standards (Strikte Pflicht)
- **Kompakte Module & Funktionen**: Dateien übersichtlich halten (Ziel: < 200 Zeilen pro Modul), Funktionen kompakt halten (Ziel: max. 20–30 Zeilen, SRP, Guard Clauses statt tiefer Verschachtelung).
- **Strikte Schichten-Trennung**:
  1. `src/domain`: Unveränderliche Datenmodelle (`@dataclass(frozen=True)`), Enums, Pydantic-KI-Schemata und typisierte Exceptions.
  2. `src/config`: Konstanten ohne Magic Values, YAML-Defaults und `AppSettings` (`repr=False` für alle Secrets).
  3. `src/security`: SSRF-Guard (inkl. DNS-Rebinding- & Redirect-Hop-Prüfung), Input-Whitelist-Validierung, HMAC-Auth & Brute-Force-Schutz.
  4. `src/storage`: `SqlExecutor`-Abstraktion + einheitliches `SqlAlertRepository` ohne dupliziertes SQL für lokales SQLite (WAL) & Turso Cloud (Hrana v2 HTTPS).
  5. `src/detectors`: Modulare Wächter-Strategien (`weather_detector`, `web_search_detector`, `price_threshold_detector`, `page_change_detector`, `http_api_detector`), Freitext-KI-Compiler (`rule_compiler`) und Deduplizierungs-/Zustands-Engine (`engine`).
  6. `src/notifications`: Erweiterbare `NotificationChannel`-Architektur, XSS-sicheres HTML-E-Mail-Rendering & Versand via Resend / SMTP.
  7. `src/worker`: GitHub Actions Dispatcher (`workflow_dispatch`), lokaler Hintergrund-Scheduler & CLI-Worker (`check_alerts.yaml` für 1–60+ Min. Intervalle inkl. `cron-job.org`-Kompatibilität).
  8. `src/ui`: Reine Streamlit-Präsentationsschicht ohne Geschäfts-, SQL- oder Scraping-Logik.

## Security Baseline (`security-standard`)
- **Zero Hardcoded Secrets**: Alle Keys/Passwörter ausschließlich über Umgebungsvariablen / `st.secrets`.
- **Parameterized SQL**: Ausnahmslos `?`-Platzhalter für SQLite und Turso.
- **SSRF & DoS Prevention**: Strikte IP-/Schema-Prüfung vor jedem HTTP-Abruf, Redirect-Validierung, explizite Timeouts und 2-MB-Payload-Größenlimits.
- **Safe Rendering & Logging**: Alle HTML-/Link-Ausgaben bereinigen; E-Mail-Adressen in Logs maskieren (`h***@domain.de`).
