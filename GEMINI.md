# Projekt-Richtlinien für Alert-Bot

## Grundregel: Automatische Zwei-Wege-Synchronisation (Remote & Lokal)

### 1. Vor jeder ersten Anfrage (Sitzungsstart)
- **Automatisches `git pull`**: Führe zu Beginn jeder neuen Konversation oder ersten Anfrage in diesem Projekt **immer zuerst `git pull`** aus, um alle zwischenzeitlich vom lokalen PC gepushten Änderungen abzurufen.
- **Rückmeldung**: Gib dem Benutzer immer eine kurze Statusmeldung (z. B. *"Repository aktualisiert"* oder *"Bereits auf dem neuesten Stand"*).

### 2. Nach jedem erfolgreich getesteten Commit (Abschluss)
- **Qualitätscheck vor jedem Commit**: Führe vor jedem Commit zwingend Linting und Tests aus:
  - Linux / Remote: `uv run --extra dev ruff check .` und `uv run --extra dev pytest`
  - Windows / Lokal: `.venv\Scripts\ruff.exe check .` und `.venv\Scripts\pytest.exe`
- **Automatisches Committen & `git push`**: Committe abgeschlossene Änderungen sauber und führe **direkt im Anschluss `git push origin main`** aus.
- **Push-Bestätigung ohne manuelle Aufforderung**: Bestätige nach dem erfolgreichen Push kurz die Fertigstellung. **Fordere den Benutzer niemals zu manuellen Git-Befehlen (wie git pull, git pull --tags etc.) auf.**
- **Automatisches Versions-Tagging & `git push --tags` (Semantic Versioning)**: Erhöhe bei Bugfixes oder Features die Version synchron in `src/__version__.py` und `pyproject.toml`. Erstelle den Git-Tag **immer automatisch direkt selbst** (`git tag -a v0.X.Y -m "Release v0.X.Y: ..."`) und pushe ihn unmittelbar mit (`git push origin v0.X.Y`), damit Tags remote und lokal ohne manuelle Zwischenschritte synchron sind.

### 3. Lokale Ausführung durch den Agenten (sofern lokal verfügbar / in Desktop-IDE)
- **Vollautomatisch selbst ausführen**: Sobald der Agent in einer lokalen Umgebung (z. B. auf dem PC in der Desktop-IDE mit Terminal-Zugriff) läuft, führt er `git pull --tags` vor Beginn und `git push --follow-tags` nach Abschluss **immer vollautomatisch selbst aus**, ohne den Benutzer jemals zu manuellen Terminal-Befehlen aufzufordern.
- **Nur bei reinem Remote-Betrieb**: Wenn der Agent auf einer entfernten Cloud-VM läuft (ohne physischen Zugriff auf die lokale Maschine des Nutzers), pusht der Agent alles inklusive Tags nach Remote und bestätigt kurz und bündig die Fertigstellung – ohne den Nutzer zu Terminal-Befehlen aufzufordern.

## Proaktive Roadmap-Erinnerung: Weitere Alarm-Kanäle (`ntfy.sh`, `Telegram`, `Webhook`)
- **Ausdrücklicher Benutzerwunsch**: In `v0.1.x` startet der Bot bewusst mit **E-Mail (Resend)** als aktivem Kanal, während die Kanal-Architektur (`NotificationChannel`-Protokoll in `src/notifications/channels.py`) bereits modular vorbereitet ist.
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
  6. `src/notifications`: Erweiterbare `NotificationChannel`-Architektur, XSS-sicheres HTML-E-Mail-Rendering & Versand via Resend.
  7. `src/worker`: GitHub Actions Dispatcher (`workflow_dispatch`), lokaler Hintergrund-Scheduler & CLI-Worker (`check_alerts.yaml` für 1–60+ Min. Intervalle inkl. `cron-job.org`-Kompatibilität).
  8. `src/ui`: Reine Streamlit-Präsentationsschicht ohne Geschäfts-, SQL- oder Scraping-Logik.

## Security Baseline (`security-standard`)
- **Zero Hardcoded Secrets**: Alle Keys/Passwörter ausschließlich über Umgebungsvariablen / `st.secrets`.
- **Parameterized SQL**: Ausnahmslos `?`-Platzhalter für SQLite und Turso.
- **SSRF & DoS Prevention**: Strikte IP-/Schema-Prüfung vor jedem HTTP-Abruf, Redirect-Validierung, explizite Timeouts und 2-MB-Payload-Größenlimits.
- **Safe Rendering & Logging**: Alle HTML-/Link-Ausgaben bereinigen; E-Mail-Adressen in Logs maskieren (`h***@domain.de`).
