"""Triggert `.github/workflows/check_alerts.yaml` über die GitHub Actions REST API."""

from __future__ import annotations

import logging

import requests

from src.config.constants import GITHUB_API_TIMEOUT_SECONDS
from src.config.settings import AppSettings
from src.domain.exceptions import DispatchError

logger = logging.getLogger(__name__)


class GitHubActionsDispatcher:
    """Löst bei Bedarf sofortige Cloud-Prüfungen über `workflow_dispatch` aus."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def trigger_check(self, rule_id: str = "", force: bool = True) -> None:
        """Startet einen asynchronen GitHub-Actions-Lauf für `rule_id` (oder alle fälligen Regeln)."""
        if not self._settings.has_github_worker_configured:
            raise DispatchError("GitHub-Worker ist nicht konfiguriert (GITHUB_TOKEN / GITHUB_REPO fehlt).")

        repo = self._settings.github_repo.strip("/")
        workflow = self._settings.github_workflow_file.strip()
        endpoint = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
        headers = {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AlertBot/0.1",
        }

        try:
            resp = requests.post(
                endpoint,
                headers=headers,
                json={
                    "ref": self._settings.github_branch,
                    "inputs": {
                        "rule_id": rule_id.strip(),
                        "force": "true" if force else "false",
                    },
                },
                timeout=GITHUB_API_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise DispatchError(f"GitHub Actions API nicht erreichbar: {exc}") from exc

        if resp.status_code != 204:
            raise DispatchError(
                f"GitHub Actions Dispatch abgelehnt (HTTP {resp.status_code}). "
                "Bitte Berechtigung `Actions: Read and write` prüfen."
            )
        logger.info("GitHub Actions Workflow '%s' ausgelöst (rule_id='%s').", workflow, rule_id or "due")
