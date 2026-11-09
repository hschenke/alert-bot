"""Root-Entrypoint für Streamlit Community Cloud und lokalen Start (`streamlit run streamlit_app.py`)."""

from __future__ import annotations

import warnings

# Unterdrückt Upstream-SyntaxWarnings aus Drittanbieter-Paketen (z. B. google.genai 3.0.0)
warnings.filterwarnings("ignore", category=SyntaxWarning, message=r".*invalid escape sequence.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r".*invalid escape sequence.*")

from src.ui.app import run_streamlit_app  # noqa: E402

if __name__ == "__main__":
    run_streamlit_app()
