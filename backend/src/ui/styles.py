"""Small, stable Streamlit style layer."""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components


def apply_styles() -> None:
    """Apply restrained layout styling without relying on fragile generated class names."""
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1280px;
            padding-top: 2rem;
            padding-bottom: 2.2rem;
        }
        h1 {
            font-size: 1.65rem !important;
            line-height: 1.25 !important;
            margin-bottom: 0.2rem !important;
        }
        h2, h3 {
            letter-spacing: 0;
        }
        div[data-testid="stMetric"] {
            background: #f6fbfa;
            border: 1px solid #d9ece9;
            border-radius: 8px;
            padding: 0.65rem 0.8rem;
        }
        div[data-testid="stSidebar"] {
            background: #eaf6f4;
        }
        .status-card {
            border: 1px solid #d9ece9;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
            background: #f8fcfb;
            margin: 0.4rem 0 0.8rem 0;
        }
        .muted {
            color: #607d7a;
            font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_intro(title: str, description: str) -> None:
    """Render a compact page heading."""
    st.subheader(title)
    st.caption(description)


def reset_main_scroll(view_key: str) -> None:
    """Return to the top when navigation or runtime selection changes."""
    encoded_view = json.dumps(view_key)
    components.html(
        f"""
        <script>
        (() => {{
          try {{
            const parentWindow = window.parent;
            const storageKey = "molecule-vision-active-view";
            const viewKey = {encoded_view};
            if (parentWindow.sessionStorage.getItem(storageKey) === viewKey) return;
            const documentRoot = parentWindow.document;
            const targets = [
              documentRoot.scrollingElement,
              documentRoot.documentElement,
              documentRoot.body,
              documentRoot.querySelector('[data-testid="stAppViewContainer"]'),
              documentRoot.querySelector('[data-testid="stMain"]'),
              documentRoot.querySelector('section.main'),
            ];
            for (const target of new Set(targets)) {{
              if (!target) continue;
              target.scrollTop = 0;
              target.scrollTo?.({{ top: 0, behavior: "auto" }});
            }}
            parentWindow.scrollTo(0, 0);
            parentWindow.sessionStorage.setItem(storageKey, viewKey);
          }} catch (_error) {{}}
        }})();
        </script>
        """,
        height=0,
    )


def status_card(message: str, tone: str = "info") -> None:
    if tone == "success":
        st.success(message)
    elif tone == "warning":
        st.warning(message)
    elif tone == "error":
        st.error(message)
    else:
        st.info(message)
