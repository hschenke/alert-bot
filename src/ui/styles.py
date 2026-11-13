"""Statisches Theme-CSS für eine übersichtliche Desktop- & Smartphone-Darstellung."""

from __future__ import annotations

import streamlit as st

_STATIC_CSS = """
<style>
/* Sauberes oberes Padding gegen abgeschnittene Inhalte unter der Streamlit-Header-Leiste */
.block-container {
    padding-top: 2.8rem;
    padding-bottom: 2.5rem;
    max-width: 1120px;
}
header[data-testid="stHeader"] {
    background: transparent;
}

/* Deutlich kompaktere und kleinere Metrik-Karten */
div[data-testid="stMetric"] {
    background: rgba(30, 41, 59, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 8px;
    padding: 6px 12px !important;
    min-height: unset !important;
}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    color: #94a3b8 !important;
    margin-bottom: 2px !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.25rem !important;
    font-weight: 600 !important;
    line-height: 1.15 !important;
    color: #f8fafc !important;
}

/* Smartphone-freundliche Navigations-Buttons in der Sidebar (Touch-Flächen, mehr Abstand, keine Radiobuttons) */
section[data-testid="stSidebar"] div.stButton > button {
    min-height: 48px !important;
    font-size: 0.98rem !important;
    font-weight: 500 !important;
    border-radius: 10px !important;
    margin-bottom: 8px !important;
    padding: 10px 16px !important;
    display: flex !important;
    justify-content: flex-start !important;
    align-items: center !important;
    text-align: left !important;
    transition: all 0.15s ease-in-out;
}
section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    border: 1px solid #3b82f6 !important;
    color: #ffffff !important;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.35);
}
section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] {
    background: rgba(30, 41, 59, 0.45) !important;
    border: 1px solid rgba(148, 163, 184, 0.15) !important;
    color: #cbd5e1 !important;
}
section[data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover {
    background: rgba(30, 41, 59, 0.8) !important;
    border-color: rgba(148, 163, 184, 0.3) !important;
    color: #ffffff !important;
}
/* Dezenter Aktions-Button im Header & in Karten (nur Secondary, Primary bleibt Warnsignal) */
div[data-testid="stColumn"] div.stButton > button[kind="secondary"] {
    background: rgba(30, 41, 59, 0.6) !important;
    border: 1px solid rgba(148, 163, 184, 0.25) !important;
    color: #e2e8f0 !important;
    font-weight: 500 !important;
    border-radius: 8px !important;
    transition: all 0.15s ease-in-out !important;
}
div[data-testid="stColumn"] div.stButton > button[kind="secondary"]:hover {
    background: rgba(51, 65, 85, 0.8) !important;
    border-color: rgba(148, 163, 184, 0.45) !important;
    color: #ffffff !important;
}
div[data-testid="stColumn"] div.stButton > button[kind="primary"]:not(:disabled) {
    background: #dc2626 !important;
    border: 1px solid #ef4444 !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
}
div[data-testid="stColumn"] div.stButton > button[kind="primary"]:not(:disabled):hover {
    background: #b91c1c !important;
    border-color: #f87171 !important;
}

/* Kompakte 4er-KPI-Leiste (passt auch auf Smartphones nebeneinander in eine Zeile) */
.ab-kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    max-width: 420px;
    width: 100%;
    margin: 0.15rem 0 0.4rem 0;
}
.ab-kpi-card {
    background: rgba(30, 41, 59, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 8px;
    padding: 5px 6px;
    text-align: center;
    min-width: 0;
}
.ab-kpi-label {
    font-size: 0.72rem;
    font-weight: 500;
    color: #94a3b8;
    line-height: 1.15;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.ab-kpi-value {
    font-size: 1.1rem;
    font-weight: 600;
    color: #f8fafc;
    line-height: 1.25;
    margin-top: 1px;
    font-variant-numeric: tabular-nums;
}
@media (max-width: 480px) {
    .ab-kpi-grid {
        gap: 5px;
    }
    .ab-kpi-card {
        padding: 4px 3px;
        border-radius: 6px;
    }
    .ab-kpi-label {
        font-size: 0.65rem;
    }
    .ab-kpi-value {
        font-size: 1rem;
    }
}

/* Kompakte, saubere Darstellung für eingeklappte Alert-Boxen & innere Log-Box */
div[data-testid="stExpander"] {
    border-radius: 10px !important;
    border-color: rgba(148, 163, 184, 0.22) !important;
    background: rgba(15, 23, 42, 0.35) !important;
    margin-bottom: 0.4rem !important;
}
div[data-testid="stExpander"] div[data-testid="stExpander"] {
    border-radius: 8px !important;
    border-color: rgba(148, 163, 184, 0.16) !important;
    background: rgba(30, 41, 59, 0.35) !important;
    margin-top: 0.35rem !important;
    margin-bottom: 0 !important;
}
</style>
"""


def inject_global_styles() -> None:
    """Injiziert ausschließlich statisches Layout-CSS (niemals Benutzereingaben)."""
    st.markdown(_STATIC_CSS, unsafe_allow_html=True)
