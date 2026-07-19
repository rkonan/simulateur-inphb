from __future__ import annotations

import hmac
import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from navigation_ui import afficher_sidebar_admin, masquer_navigation_native

st.set_page_config(
    page_title="Administration INP-HB",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB = Path(os.getenv("INPHB_DB", "population_inphb.db"))
SESSION_KEY = "inphb_admin_authenticated"


def lire_mot_de_passe_admin() -> str:
    valeur = os.getenv("ADMIN_PASSWORD", "").strip()
    if valeur:
        return valeur
    try:
        return str(st.secrets["ADMIN_PASSWORD"]).strip()
    except (KeyError, FileNotFoundError):
        return ""


def verifier_acces_admin() -> bool:
    attendu = lire_mot_de_passe_admin()
    if not attendu:
        st.error(
            "Accès administrateur non configuré. Définis la variable "
            "d’environnement `ADMIN_PASSWORD` ou ajoute-la dans `.streamlit/secrets.toml`."
        )
        return False

    if st.session_state.get(SESSION_KEY, False):
        return True

    masquer_navigation_native()
    st.title("🔐 Administration")
    st.caption("Cette zone est réservée à l’administrateur de l’application.")

    with st.form("admin_login", clear_on_submit=False):
        mot_de_passe = st.text_input("Mot de passe", type="password", autocomplete="current-password")
        connexion = st.form_submit_button("Se connecter", type="primary", use_container_width=True)

    if connexion:
        if hmac.compare_digest(mot_de_passe, attendu):
            st.session_state[SESSION_KEY] = True
            st.rerun()
        st.error("Mot de passe incorrect.")
    return False


if not verifier_acces_admin():
    st.stop()

afficher_sidebar_admin()
with st.sidebar:
    if st.button("Se déconnecter", icon="🚪", use_container_width=True):
        st.session_state.pop(SESSION_KEY, None)
        st.rerun()


st.title("Administration du modèle V4")
with sqlite3.connect(DB) as con:
    meta = pd.read_sql_query("SELECT * FROM model_metadata", con)
    series = pd.read_sql_query("SELECT DISTINCT serie FROM candidats ORDER BY serie", con)["serie"].tolist()
    matieres = pd.read_sql_query("SELECT DISTINCT matiere FROM notes_candidats ORDER BY matiere", con)["matiere"].tolist()
    filieres = pd.read_sql_query("SELECT DISTINCT filiere FROM scores ORDER BY filiere", con)["filiere"].tolist()
    groupes = pd.read_sql_query("SELECT DISTINCT groupe_reference FROM candidats ORDER BY groupe_reference", con)["groupe_reference"].tolist()

st.dataframe(meta, width="stretch", hide_index=True)

with sqlite3.connect(DB) as con:
    repartition = pd.read_sql_query(
        """
        SELECT groupe_reference, mention, COUNT(*) AS effectif
        FROM candidats
        GROUP BY groupe_reference, mention
        ORDER BY groupe_reference, mention
        """,
        con,
    )
st.subheader("Répartition des mentions par groupe de référence")
st.dataframe(repartition, width="stretch", hide_index=True)

c1, c2, c3, c4 = st.columns(4)
serie = c1.selectbox("Série", ["Toutes"] + series)
groupe = c2.selectbox("Groupe", ["Tous"] + groupes)
matiere = c3.selectbox("Matière", matieres)
variable = c4.selectbox("Variable", ["note_bac", "moyenne_2nde", "moyenne_1ere", "moyenne_terminale", "mc", "mgm"])

conditions = ["n.matiere=?"]
parametres = [matiere]
if serie != "Toutes":
    conditions.append("c.serie=?")
    parametres.append(serie)
if groupe != "Tous":
    conditions.append("c.groupe_reference=?")
    parametres.append(groupe)

with sqlite3.connect(DB) as con:
    df = pd.read_sql_query(
        f"""
        SELECT c.serie, c.mention, c.groupe_reference, n.*
        FROM notes_candidats n
        JOIN candidats c USING(candidate_id)
        WHERE {' AND '.join(conditions)}
        """,
        con,
        params=parametres,
    )

st.subheader("Statistiques descriptives")
st.dataframe(
    df[["note_bac", "moyenne_2nde", "moyenne_1ere", "moyenne_terminale", "mc", "mgm"]].describe().T,
    width="stretch",
)
fig, ax = plt.subplots()
ax.hist(df[variable].dropna(), bins=30)
ax.set_xlabel(variable)
ax.set_ylabel("Profils")
st.pyplot(fig)

st.subheader("Par mention et groupe")
st.dataframe(
    df.groupby(["groupe_reference", "mention"])[variable]
    .agg(["count", "mean", "std", "min", "median", "max"])
    .reset_index(),
    width="stretch",
    hide_index=True,
)

st.subheader("Scores par filière")
filiere = st.selectbox("Filière", filieres)
with sqlite3.connect(DB) as con:
    scores = pd.read_sql_query(
        """
        SELECT s.score, c.serie, c.mention, c.groupe_reference
        FROM scores s
        JOIN candidats c USING(candidate_id)
        WHERE s.filiere=?
        """,
        con,
        params=(filiere,),
    )
fig2, ax2 = plt.subplots()
for nom_groupe, sous_df in scores.groupby("groupe_reference"):
    ax2.hist(sous_df["score"].dropna(), bins=30, alpha=0.5, label=nom_groupe)
ax2.set_xlabel("Score dossier")
ax2.set_ylabel("Profils")
ax2.legend()
st.pyplot(fig2)
st.dataframe(
    scores.groupby(["groupe_reference", "serie", "mention"])["score"]
    .agg(["count", "mean", "std", "min", "max"])
    .reset_index(),
    width="stretch",
    hide_index=True,
)
