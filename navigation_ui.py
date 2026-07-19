from __future__ import annotations

import streamlit as st


_NAV_CSS = """
<style>
/* Masque systématiquement la navigation multipage native de Streamlit.
   La navigation publique est entièrement contrôlée ci-dessous. */
[data-testid="stSidebarNav"],
[data-testid="stSidebarNavItems"],
section[data-testid="stSidebar"] nav {
    display: none !important;
}

[data-testid="stSidebar"] {
    min-width: 270px;
}

[data-testid="stSidebar"] .stPageLink a {
    border-radius: 10px;
    padding: 0.58rem 0.68rem;
    font-weight: 600;
}

.sidebar-brand {
    margin-bottom: 1.1rem;
}
.sidebar-brand-title {
    font-size: 1.18rem;
    font-weight: 750;
    line-height: 1.25;
    margin-bottom: .2rem;
}
.sidebar-brand-subtitle {
    color: #6b7280;
    font-size: .88rem;
}
.sidebar-footer {
    color: #7b8491;
    font-size: .78rem;
    line-height: 1.4;
    padding-top: .35rem;
}
</style>
"""


def masquer_navigation_native() -> None:
    st.markdown(_NAV_CSS, unsafe_allow_html=True)


def afficher_sidebar_publique() -> None:
    """Sidebar publique volontairement minimale, sans aucune entrée Admin."""
    masquer_navigation_native()
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
              <div class="sidebar-brand-title">🎓 Assistant INP-HB</div>
              <div class="sidebar-brand-subtitle">Analyser · Progresser</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("app.py", label="Analyse du dossier", icon="🎓")
        st.page_link("pages/01_Projection.py", label="Projection de progression", icon="📈")
        st.divider()
        st.markdown(
            '<div class="sidebar-footer">Outil indicatif et indépendant de l’INP-HB.</div>',
            unsafe_allow_html=True,
        )


def afficher_sidebar_admin() -> None:
    """Sidebar de la zone privée, affichée uniquement après authentification."""
    masquer_navigation_native()
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
              <div class="sidebar-brand-title">🔐 Administration</div>
              <div class="sidebar-brand-subtitle">Zone privée</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("app.py", label="Retour à la simulation", icon="⬅️")
        st.page_link("pages/01_Projection.py", label="Projection", icon="📈")
