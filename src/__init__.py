"""Alert-Bot – Autonomer 24/7 Überwachungs- & Benachrichtigungs-Assistent."""

from __future__ import annotations

import warnings

# Unterdrückt Upstream-SyntaxWarnings/DeprecationWarnings (z. B. invalid escape sequence in google.genai)
warnings.filterwarnings("ignore", category=SyntaxWarning, message=r".*invalid escape sequence.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r".*invalid escape sequence.*")
