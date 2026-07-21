from __future__ import annotations

import base64
import io
import json
import os
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from navigation_ui import afficher_sidebar_publique

from simulateur_core import (
    calculer_candidat,
    charger_parametres,
    evaluer_admissibilite_depuis_db,
    filieres_autorisees_serie,
    lignes_formule,
    matieres_reelles_pour_formules,
    slug,
)

st.set_page_config(
    page_title="Projection d'admissibilité INP-HB",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
[data-testid="stSidebar"] {min-width:285px;}
[data-testid="stSidebar"] .stPageLink a {border-radius:10px;padding:.55rem .65rem;}
.projection-banner {padding:1rem 1.1rem;border-radius:14px;background:#f3f7fb;border-left:5px solid #2f6fab;margin-bottom:1rem;}
.result-card {border:1px solid #e5e7eb;border-radius:14px;padding:0.9rem 1rem;background:white;min-height:150px;}
.result-card h4 {margin:0 0 .35rem 0;}
.positive {color:#137333;font-weight:700;}
.negative {color:#b3261e;font-weight:700;}
.neutral {color:#5f6368;font-weight:700;}
.impact-high {font-weight:700;}
@media (max-width:767px){.result-card{min-height:auto;}}
</style>
""",
    unsafe_allow_html=True,
)

afficher_sidebar_publique()


PARAMS_PATH = Path(os.getenv("INPHB_PARAMS", "parametres_simulateur_inphb.xlsx"))
DB_PATH = Path(os.getenv("INPHB_DISTRIBUTIONS_DB", "population_inphb_distributions.db"))
NB_DOSSIERS = 3000
SEUIL = 1700
QUERY_STATE_KEY = "projection_state"


def charger_etat_projection_url() -> dict[str, Any]:
    """Recharge l'état sauvegardé dans l'URL après un rafraîchissement navigateur."""
    raw = st.query_params.get(QUERY_STATE_KEY)
    if not raw:
        return {}
    try:
        compressed = base64.urlsafe_b64decode(str(raw).encode("ascii"))
        decoded = zlib.decompress(compressed).decode("utf-8")
        state = json.loads(decoded)
        return state if isinstance(state, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError, zlib.error):
        return {}


def sauvegarder_etat_projection_url(state: dict[str, Any]) -> None:
    """Sauvegarde un état compact dans l'URL sans dépendre du session_state."""
    try:
        payload = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        compressed = zlib.compress(payload, level=9)
        encoded = base64.urlsafe_b64encode(compressed).decode("ascii")
        if st.query_params.get(QUERY_STATE_KEY) != encoded:
            st.query_params[QUERY_STATE_KEY] = encoded
    except (TypeError, ValueError):
        # Une erreur de sérialisation ne doit jamais bloquer le simulateur.
        pass


etat_url = charger_etat_projection_url()


def fmt(value: float | None, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}".replace(".", ",")


def niveau_probabilite(p: float | None) -> str:
    if p is None or pd.isna(p):
        return "Non calculé"
    if p >= 80:
        return "Très favorable"
    if p >= 60:
        return "Favorable"
    if p >= 40:
        return "À confirmer"
    if p >= 20:
        return "À renforcer"
    return "Sélectif"


def evaluer_scores(scores: dict[str, float], serie: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for filiere, score in scores.items():
        result = evaluer_admissibilite_depuis_db(
            DB_PATH,
            filiere,
            serie,
            score,
            NB_DOSSIERS,
            SEUIL,
        )
        rows.append(
            {
                "Filière": filiere,
                "Score": float(score),
                "Probabilité": result.get("probabilite_exacte") if result.get("disponible") else np.nan,
                "Rang moyen": result.get("rang_moyen") if result.get("disponible") else np.nan,
                "Marge": (SEUIL - float(result["rang_moyen"])) if result.get("disponible") else np.nan,
                "Disponible": bool(result.get("disponible")),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["Probabilité", "Score"], ascending=False, na_position="last").reset_index(drop=True)


def completed_years(level: str) -> list[str]:
    return {
        "Seconde": ["2nde"],
        "Première": ["2nde", "1ere"],
        "Terminale": ["2nde", "1ere", "tle"],
    }[level]


def build_profiles(
    table: pd.DataFrame,
    level: str,
    global_progress: float,
    bac_bonus: float,
    subject_adjustments: dict[str, float],
) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, float], dict[str, dict[str, float]]]:
    current_bac: dict[str, float] = {}
    current_means: dict[str, dict[str, float]] = {}
    projected_bac: dict[str, float] = {}
    projected_means: dict[str, dict[str, float]] = {}

    years_done = completed_years(level)
    for _, row in table.iterrows():
        mat = str(row["Matière"])
        known = {year: float(row[year]) for year in years_done}
        latest = known[years_done[-1]]
        adj = float(subject_adjustments.get(mat, 0.0))

        # Situation actuelle : on fige les années futures et le bac au dernier niveau connu.
        current_means[mat] = {
            "2nde": known.get("2nde", latest),
            "1ere": known.get("1ere", latest),
            "tle": known.get("tle", latest),
        }
        current_bac[mat] = latest

        projected = dict(current_means[mat])
        if level == "Seconde":
            projected["1ere"] = np.clip(latest + 0.55 * global_progress + 0.55 * adj, 0, 20)
            projected["tle"] = np.clip(latest + global_progress + adj, 0, 20)
        elif level == "Première":
            projected["tle"] = np.clip(latest + global_progress + adj, 0, 20)
        else:
            projected["tle"] = known["tle"]

        terminal_target = float(projected["tle"])
        projected_means[mat] = {key: float(value) for key, value in projected.items()}
        # En Terminale, la moyenne de Terminale est déjà connue : l'ajustement matière
        # représente directement l'effort ciblé attendu au Bac. Pour les niveaux
        # antérieurs, il reste partiellement intégré à la projection finale du Bac.
        bac_subject_adjustment = adj if level == "Terminale" else 0.25 * adj
        projected_bac[mat] = float(np.clip(terminal_target + bac_bonus + bac_subject_adjustment, 0, 20))

    return current_bac, current_means, projected_bac, projected_means


def make_report_pdf(
    serie: str,
    level: str,
    target: str,
    comparison: pd.DataFrame,
    impact_global: pd.DataFrame,
    impacts_by_filiere: dict[str, pd.DataFrame],
    plan: pd.DataFrame,
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        return b""

    stream = io.BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=1.25 * cm,
        leftMargin=1.25 * cm,
        topMargin=1.1 * cm,
        bottomMargin=1.1 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Rapport de projection INP-HB", styles["Title"]),
        Paragraph(
            f"Série : {serie} — Niveau : {level} — Filière objectif : {target}",
            styles["Normal"],
        ),
        Spacer(1, 10),
        Paragraph("1. Synthèse de toutes les filières", styles["Heading2"]),
    ]

    synthese_data = [[
        "Filière", "Score actuel", "Score projeté", "Prob. projetée", "Rang projeté", "Gain de places"
    ]]
    for _, row in comparison.iterrows():
        synthese_data.append([
            str(row["Filière"]),
            fmt(row["Score actuelle"], 2),
            fmt(row["Score projetée"], 2),
            fmt(row["Probabilité projetée"], 1) + " %",
            fmt(row["Rang moyen projetée"], 0),
            fmt(row["Gain de places"], 0),
        ])
    synthese_table = Table(
        synthese_data,
        repeatRows=1,
        colWidths=[4.0*cm, 2.1*cm, 2.1*cm, 2.2*cm, 2.1*cm, 2.1*cm],
    )
    synthese_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2F6FAB")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F7F9FC")]),
    ]))
    story.extend([synthese_table, Spacer(1, 12)])

    story.append(Paragraph("2. Priorités globales par matière", styles["Heading2"]))
    global_data = [[
        "Matière", "Gain score moyen", "Gain prob. moyen", "Gain moyen de rang", "Filières améliorées"
    ]]
    for _, row in impact_global.head(12).iterrows():
        global_data.append([
            str(row["Matière"]),
            fmt(row["Gain score moyen"], 3),
            fmt(row["Gain probabilité moyenne"], 2) + " pt",
            fmt(row["Gain moyen de rang"], 1),
            str(row["Filières améliorées"]),
        ])
    global_table = Table(
        global_data,
        repeatRows=1,
        colWidths=[3.7*cm, 2.8*cm, 3.0*cm, 3.0*cm, 3.0*cm],
    )
    global_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EAF1F8")),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.extend([global_table, Spacer(1, 12)])

    story.append(Paragraph("3. Analyse détaillée par filière", styles["Heading2"]))
    for index, filiere in enumerate(comparison["Filière"].astype(str).tolist()):
        filiere_row = comparison[comparison["Filière"] == filiere].iloc[0]
        story.append(Paragraph(f"{filiere}", styles["Heading3"]))
        story.append(Paragraph(
            "Score projeté : "
            f"<b>{fmt(filiere_row['Score projetée'], 2)}</b> — "
            "Probabilité projetée : "
            f"<b>{fmt(filiere_row['Probabilité projetée'], 1)} %</b> — "
            "Rang projeté : "
            f"<b>{fmt(filiere_row['Rang moyen projetée'], 0)}</b> — "
            "Gain de places : "
            f"<b>{fmt(filiere_row['Gain de places'], 0)}</b>",
            styles["Normal"],
        ))
        impact_f = impacts_by_filiere.get(filiere, pd.DataFrame()).head(8)
        detail_data = [["Matière", "Gain score +1", "Gain prob. +1", "Gain de rang +1"]]
        for _, row in impact_f.iterrows():
            detail_data.append([
                str(row["Matière"]),
                fmt(row["Gain score +1"], 3),
                fmt(row["Gain probabilité +1"], 2) + " pt",
                fmt(row["Gain de rang +1"], 1),
            ])
        detail_table = Table(
            detail_data,
            repeatRows=1,
            colWidths=[4.5*cm, 3.4*cm, 3.4*cm, 3.4*cm],
        )
        detail_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F1F4F8")),
            ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
            ("FONTSIZE", (0,0), (-1,-1), 7.5),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.extend([Spacer(1, 4), detail_table, Spacer(1, 10)])
        if index and index % 3 == 2:
            story.append(PageBreak())

    story.extend([Spacer(1, 6), Paragraph("4. Plan de progression", styles["Heading2"])])
    plan_data = [["Matière", "Niveau actuel", "Objectif Terminale", "Objectif Bac", "Effort total"]]
    for _, row in plan.iterrows():
        plan_data.append([
            str(row["Matière"]),
            fmt(row["Niveau actuel"], 1),
            fmt(row["Objectif Terminale"], 1),
            fmt(row["Objectif Bac"], 1),
            fmt(row["Effort total"], 1),
        ])
    plan_table = Table(plan_data, repeatRows=1, colWidths=[4.6*cm, 2.7*cm, 3.2*cm, 2.7*cm, 2.5*cm])
    plan_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EAF1F8")),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
    ]))
    story.extend([plan_table, Spacer(1, 12)])
    story.append(Paragraph(
        "Résultats indicatifs, sans valeur officielle et dépendants des hypothèses de projection.",
        styles["Italic"],
    ))
    doc.build(story)
    return stream.getvalue()

st.title("📈 Projection d'admissibilité par niveau")
st.markdown(
    """
<div class="projection-banner">
Simule l'évolution de ton dossier depuis la Seconde, la Première ou la Terminale. Les curseurs permettent de mesurer immédiatement l'effet d'une progression sur tes scores, tes rangs et tes chances estimées par filière.
<br><strong>Cette projection est indicative : elle repose sur des notes futures hypothétiques.</strong>
</div>
""",
    unsafe_allow_html=True,
)

if not PARAMS_PATH.exists():
    st.error("Le fichier parametres_simulateur_inphb.xlsx est absent.")
    st.stop()
params = charger_parametres(PARAMS_PATH)

profile_cols = st.columns(3)

level_options = ["Seconde", "Première", "Terminale"]
saved_level = str(etat_url.get("level", ""))
level_index = level_options.index(saved_level) if saved_level in level_options else 0
level = profile_cols[0].selectbox(
    "Ton niveau actuel",
    level_options,
    index=level_index,
)

serie_options = sorted(params.coeffs_bac)
saved_serie = str(etat_url.get("serie", ""))
serie_index = serie_options.index(saved_serie) if saved_serie in serie_options else 0
serie = profile_cols[1].selectbox(
    "Série de bac visée",
    serie_options,
    index=serie_index,
)

mention_options = params.mentions["mention"].astype(str).tolist()
saved_mention = str(etat_url.get("mention", ""))
mention_index = (
    mention_options.index(saved_mention)
    if saved_mention in mention_options
    else min(2, len(mention_options) - 1)
)
mention = profile_cols[2].selectbox(
    "Mention cible au bac",
    mention_options,
    index=mention_index,
)

compatible = filieres_autorisees_serie(params, serie)
calculable: list[str] = []
for f in compatible:
    try:
        lignes_formule(params, f, serie)
        calculable.append(f)
    except ValueError:
        pass
if not calculable:
    st.warning("Aucune filière calculable pour cette série.")
    st.stop()

saved_target = str(etat_url.get("target", ""))
target_index = calculable.index(saved_target) if saved_target in calculable else 0
target = st.selectbox(
    "🎯 Filière objectif",
    calculable,
    index=target_index,
)

saved_selected = [
    str(value)
    for value in etat_url.get("selected", [])
    if str(value) in calculable
]
selected = st.multiselect(
    "Filières à comparer",
    calculable,
    default=saved_selected or calculable,
    key=f"filieres_comparees_{serie}",
)
if not selected:
    st.stop()
matieres = matieres_reelles_pour_formules(params, serie, calculable)

st.subheader("1. Situation scolaire actuelle")
years = completed_years(level)
saved_current_notes = etat_url.get("current_notes", {})
same_saved_profile = (
    etat_url.get("level") == level
    and etat_url.get("serie") == serie
)

initial_rows = []
for mat in matieres:
    saved_row = (
        saved_current_notes.get(mat, {})
        if same_saved_profile and isinstance(saved_current_notes, dict)
        else {}
    )
    row = {
        "Matière": mat,
        "2nde": float(saved_row.get("2nde", 10.0)),
        "1ere": float(saved_row.get("1ere", 10.0)),
        "tle": float(saved_row.get("tle", 10.0)),
    }
    initial_rows.append(row)
base = pd.DataFrame(initial_rows)
visible_cols = ["Matière"] + years
edited = st.data_editor(
    base[visible_cols],
    hide_index=True,
    width="stretch",
    disabled=["Matière"],
    column_config={
        "Matière": st.column_config.TextColumn("Matière"),
        "2nde": st.column_config.NumberColumn("Moyenne 2nde", min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
        "1ere": st.column_config.NumberColumn("Moyenne 1ère", min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
        "tle": st.column_config.NumberColumn("Moyenne Terminale", min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
    },
    key=f"projection_notes_{serie}_{level}",
)

st.subheader("2. Hypothèses de progression")
saved_global_progress = float(etat_url.get("global_progress", 1.0)) if same_saved_profile else 1.0
saved_bac_bonus = float(etat_url.get("bac_bonus", 0.5)) if same_saved_profile else 0.5

if level == "Terminale":
    global_progress = 0.0
    bac_bonus = st.slider(
        "Écart Bac par rapport à la moyenne de Terminale",
        -2.0,
        3.0,
        float(np.clip(saved_bac_bonus, -2.0, 3.0)),
        0.25,
        help="Hypothèse générale appliquée aux notes du Bac à partir de tes moyennes actuelles de Terminale.",
    )
else:
    control_cols = st.columns(2)
    global_progress = control_cols[0].slider(
        "Progression jusqu'à la Terminale",
        -2.0,
        5.0,
        float(np.clip(saved_global_progress, -2.0, 5.0)),
        0.25,
        help="Progression générale appliquée aux années scolaires encore à venir.",
    )
    bac_bonus = control_cols[1].slider(
        "Écart Bac par rapport à la Terminale",
        -2.0,
        3.0,
        float(np.clip(saved_bac_bonus, -2.0, 3.0)),
        0.25,
    )

subject_adjustments: dict[str, float] = {}
with st.expander("🎛 Ajuster la progression matière par matière", expanded=True):
    if level == "Terminale":
        st.caption("Ces curseurs ajustent directement la note de Bac projetée de chaque matière.")
    else:
        st.caption("Ces curseurs s'ajoutent à la progression générale. Ils permettent de tester un effort ciblé.")
    saved_adjustments = (
        etat_url.get("subject_adjustments", {})
        if same_saved_profile
        else {}
    )
    columns = st.columns(2)
    for index, mat in enumerate(matieres):
        saved_adjustment = (
            float(saved_adjustments.get(mat, 0.0))
            if isinstance(saved_adjustments, dict)
            else 0.0
        )
        subject_adjustments[mat] = columns[index % 2].slider(
            mat,
            -3.0,
            5.0,
            float(np.clip(saved_adjustment, -3.0, 5.0)),
            0.25,
            key=f"evolution_{serie}_{slug(mat)}",
        )

current_bac, current_means, automatic_bac, automatic_means = build_profiles(
    edited, level, global_progress, bac_bonus, subject_adjustments
)

st.subheader("3. Projection globale des notes")
st.caption(
    "Les hypothèses ci-dessus préremplissent automatiquement le tableau. "
    "Tu peux ensuite modifier directement une, plusieurs ou toutes les notes projetées. "
    "Les scores, probabilités, rangs, scénarios et le PDF utiliseront les valeurs du tableau."
)

automatic_rows: list[dict[str, Any]] = []
for _, row in edited.iterrows():
    mat = str(row["Matière"])
    note_actuelle = float(row[years[-1]])
    automatic_rows.append(
        {
            "Matière": mat,
            "Note actuelle": note_actuelle,
            "Progression générale": 0.0 if level == "Terminale" else float(global_progress),
            "Ajustement matière": float(subject_adjustments.get(mat, 0.0)),
            "2nde": float(automatic_means[mat]["2nde"]),
            "1ère": float(automatic_means[mat]["1ere"]),
            "Terminale projetée": float(automatic_means[mat]["tle"]),
            "Bac projeté": float(automatic_bac[mat]),
            "Évolution finale": float(automatic_bac[mat] - note_actuelle),
        }
    )
automatic_projection = pd.DataFrame(automatic_rows)

# Un contexte distinct évite qu'une saisie d'une série ou d'un niveau soit
# réutilisée accidentellement dans un autre profil.
projection_context = f"{serie}|{level}|{'|'.join(matieres)}"
context_key = "projection_editable_context"
manual_key = "projection_editable_values"
baseline_key = "projection_automatic_baseline"
version_key = "projection_editor_version"

if st.session_state.get(context_key) != projection_context:
    st.session_state[context_key] = projection_context

    restored_projection = automatic_projection.copy()
    saved_projection_rows = etat_url.get("projected_notes", [])
    if (
        same_saved_profile
        and isinstance(saved_projection_rows, list)
        and saved_projection_rows
    ):
        try:
            saved_projection = pd.DataFrame(saved_projection_rows)
            required = {"Matière", "Bac projeté"}
            if required.issubset(saved_projection.columns):
                saved_projection = saved_projection.set_index("Matière")
                restored_projection = restored_projection.set_index("Matière")
                common_subjects = restored_projection.index.intersection(saved_projection.index)
                for column in (
                    "1ère",
                    "Terminale projetée",
                    "Bac projeté",
                    "Évolution finale",
                ):
                    if column in saved_projection.columns:
                        restored_projection.loc[common_subjects, column] = (
                            saved_projection.loc[common_subjects, column]
                        )
                restored_projection = restored_projection.reset_index()
        except (TypeError, ValueError, KeyError):
            restored_projection = automatic_projection.copy()

    st.session_state[manual_key] = restored_projection
    st.session_state[baseline_key] = automatic_projection.copy()
    st.session_state[version_key] = int(st.session_state.get(version_key, 0)) + 1

button_cols = st.columns([1, 1, 2])
recalculate = button_cols[0].button(
    "🔄 Recalculer les projections",
    help="Applique les curseurs actuels et remplace toutes les notes projetées du tableau.",
    use_container_width=True,
)
restore = button_cols[1].button(
    "↩ Restaurer les valeurs automatiques",
    help="Annule les modifications manuelles et revient au dernier scénario automatique appliqué.",
    use_container_width=True,
)

if recalculate:
    st.session_state[manual_key] = automatic_projection.copy()
    st.session_state[baseline_key] = automatic_projection.copy()
    st.session_state[version_key] += 1
elif restore:
    st.session_state[manual_key] = st.session_state[baseline_key].copy()
    st.session_state[version_key] += 1

def appliquer_modifications_projection(
    editor_key: str,
    dataframe_key: str,
) -> None:
    """
    Applique les cellules modifiées au DataFrame de référence avant le rerun.

    Streamlit exécute ce callback avant de relancer tout le script. Cela évite
    que le tableau soit reconstruit à partir des projections automatiques lors
    du premier rafraîchissement suivant une saisie.
    """
    editor_state = st.session_state.get(editor_key, {})
    edited_rows = editor_state.get("edited_rows", {})

    if not edited_rows or dataframe_key not in st.session_state:
        return

    dataframe = st.session_state[dataframe_key].copy().reset_index(drop=True)

    for row_index_raw, changes in edited_rows.items():
        try:
            row_index = int(row_index_raw)
        except (TypeError, ValueError):
            continue

        if row_index < 0 or row_index >= len(dataframe):
            continue

        for column, value in changes.items():
            if column not in dataframe.columns:
                continue
            dataframe.at[row_index, column] = value

    dataframe["Évolution finale"] = (
        pd.to_numeric(dataframe["Bac projeté"], errors="coerce")
        - pd.to_numeric(dataframe["Note actuelle"], errors="coerce")
    )

    st.session_state[dataframe_key] = dataframe


if level == "Seconde":
    projection_columns = [
        "Matière", "Note actuelle", "Progression générale", "Ajustement matière",
        "1ère", "Terminale projetée", "Bac projeté", "Évolution finale",
    ]
    editable_columns = ["1ère", "Terminale projetée", "Bac projeté"]
elif level == "Première":
    projection_columns = [
        "Matière", "Note actuelle", "Progression générale", "Ajustement matière",
        "Terminale projetée", "Bac projeté", "Évolution finale",
    ]
    editable_columns = ["Terminale projetée", "Bac projeté"]
else:
    projection_columns = [
        "Matière", "Note actuelle", "Ajustement matière",
        "Bac projeté", "Évolution finale",
    ]
    editable_columns = ["Bac projeté"]

editor_key = f"projection_editor_{st.session_state[version_key]}"

st.data_editor(
    st.session_state[manual_key][projection_columns],
    hide_index=True,
    width="stretch",
    disabled=[column for column in projection_columns if column not in editable_columns],
    num_rows="fixed",
    column_config={
        "Matière": st.column_config.TextColumn("Matière"),
        "Note actuelle": st.column_config.NumberColumn(format="%.2f"),
        "Progression générale": st.column_config.NumberColumn(format="%+.2f"),
        "Ajustement matière": st.column_config.NumberColumn(format="%+.2f"),
        "1ère": st.column_config.NumberColumn("Première projetée", min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
        "Terminale projetée": st.column_config.NumberColumn(min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
        "Bac projeté": st.column_config.NumberColumn(min_value=0.0, max_value=20.0, step=0.25, format="%.2f"),
        "Évolution finale": st.column_config.NumberColumn(format="%+.2f"),
    },
    key=editor_key,
    on_change=appliquer_modifications_projection,
    args=(editor_key, manual_key),
)

# Le callback a déjà fusionné la cellule modifiée dans le DataFrame complet.
# On repart donc directement de cet état, sans réinjecter la valeur de retour
# du data_editor, qui pouvait encore correspondre au rendu précédent.
projection_notes = st.session_state[manual_key].copy()
projection_notes["Évolution finale"] = (
    pd.to_numeric(projection_notes["Bac projeté"], errors="coerce")
    - pd.to_numeric(projection_notes["Note actuelle"], errors="coerce")
)
st.session_state[manual_key] = projection_notes.copy()

# Les valeurs saisies dans le tableau deviennent l'unique source de vérité
# pour tous les calculs situés en dessous.
projected_means = {mat: dict(automatic_means[mat]) for mat in matieres}
projected_bac = dict(automatic_bac)
for _, row in projection_notes.iterrows():
    mat = str(row["Matière"])
    if level == "Seconde":
        projected_means[mat]["1ere"] = float(np.clip(row["1ère"], 0, 20))
    if level != "Terminale":
        projected_means[mat]["tle"] = float(np.clip(row["Terminale projetée"], 0, 20))
    projected_bac[mat] = float(np.clip(row["Bac projeté"], 0, 20))

baseline = st.session_state[baseline_key].set_index("Matière")
manual_indexed = projection_notes.set_index("Matière")
modified_subjects = []
for mat in matieres:
    compared_columns = editable_columns
    if any(
        not np.isclose(
            float(manual_indexed.loc[mat, column]),
            float(baseline.loc[mat, column]),
            atol=1e-9,
        )
        for column in compared_columns
    ):
        modified_subjects.append(mat)

if modified_subjects:
    st.info(
        f"✍️ **{len(modified_subjects)} matière(s) ajustée(s) manuellement** : "
        + ", ".join(modified_subjects)
    )
else:
    st.caption("Aucune note projetée n'a été ajustée manuellement.")

# Persistance après F5 : l'état utile est compressé dans l'URL.
current_notes_payload: dict[str, dict[str, float]] = {}
for _, current_row in edited.iterrows():
    mat = str(current_row["Matière"])
    current_notes_payload[mat] = {
        year: float(current_row[year])
        for year in years
    }

projection_payload = projection_notes.copy()
numeric_projection_columns = [
    column
    for column in projection_payload.columns
    if column != "Matière"
]
for column in numeric_projection_columns:
    projection_payload[column] = pd.to_numeric(
        projection_payload[column],
        errors="coerce",
    ).round(4)

sauvegarder_etat_projection_url(
    {
        "level": level,
        "serie": serie,
        "mention": mention,
        "target": target,
        "selected": selected,
        "global_progress": float(global_progress),
        "bac_bonus": float(bac_bonus),
        "subject_adjustments": {
            mat: float(value)
            for mat, value in subject_adjustments.items()
        },
        "current_notes": current_notes_payload,
        "projected_notes": projection_payload.to_dict("records"),
    }
)

_, current_scores_all = calculer_candidat(params, serie, mention, current_bac, current_means, calculable)
_, projected_scores_all = calculer_candidat(params, serie, mention, projected_bac, projected_means, calculable)
current_scores = {f: current_scores_all[f] for f in selected if f in current_scores_all}
projected_scores = {f: projected_scores_all[f] for f in selected if f in projected_scores_all}
current_result = evaluer_scores(current_scores, serie)
projected_result = evaluer_scores(projected_scores, serie)

comparison = current_result.merge(projected_result, on="Filière", suffixes=(" actuelle", " projetée"))
comparison["Gain score"] = comparison["Score projetée"] - comparison["Score actuelle"]
comparison["Gain probabilité"] = comparison["Probabilité projetée"] - comparison["Probabilité actuelle"]
comparison["Gain de places"] = comparison["Rang moyen actuelle"] - comparison["Rang moyen projetée"]
comparison = comparison.sort_values(["Probabilité projetée", "Score projetée"], ascending=False, na_position="last")

st.subheader("4. Impact sur l'admissibilité")
obj = comparison[comparison["Filière"] == target]
if not obj.empty:
    r = obj.iloc[0]
    metrics = st.columns(4)
    metrics[0].metric("Score actuel", fmt(r["Score actuelle"], 2), f"{fmt(r['Gain score'],2)} projeté")
    metrics[1].metric("Probabilité actuelle", fmt(r["Probabilité actuelle"], 1) + " %", fmt(r["Gain probabilité"], 1) + " pt")
    metrics[2].metric("Rang moyen projeté", fmt(r["Rang moyen projetée"], 0), fmt(r["Gain de places"], 0) + " places gagnées")
    metrics[3].metric("Niveau projeté", niveau_probabilite(r["Probabilité projetée"]))

chart_data = comparison.set_index("Filière")[["Probabilité actuelle", "Probabilité projetée"]].rename(
    columns={"Probabilité actuelle": "Aujourd'hui", "Probabilité projetée": "Projection"}
)
st.bar_chart(chart_data, y_label="Probabilité estimée (%)", horizontal=True)

display = comparison[[
    "Filière", "Score actuelle", "Score projetée", "Gain score",
    "Probabilité actuelle", "Probabilité projetée", "Gain probabilité",
    "Rang moyen projetée", "Gain de places",
]].copy()
st.dataframe(
    display,
    hide_index=True,
    width="stretch",
    column_config={
        "Score actuelle": st.column_config.NumberColumn(format="%.2f"),
        "Score projetée": st.column_config.NumberColumn(format="%.2f"),
        "Gain score": st.column_config.NumberColumn(format="%+.2f"),
        "Probabilité actuelle": st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=100),
        "Probabilité projetée": st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=100),
        "Gain probabilité": st.column_config.NumberColumn(format="%+.1f pt"),
        "Rang moyen projetée": st.column_config.NumberColumn(format="%.0f"),
        "Gain de places": st.column_config.NumberColumn(format="%+.0f"),
    },
)

st.subheader("5. Matières à plus fort impact")
st.caption(
    "Simule l'effet d'un gain d'un point en Terminale et au Bac. "
    "L'analyse peut porter sur toutes les filières comparées ou sur une filière précise."
)

analysis_mode = st.radio(
    "Périmètre de l'analyse",
    ["Toutes les filières sélectionnées", "Une filière spécifique"],
    horizontal=True,
)
analysis_target = target
if analysis_mode == "Une filière spécifique":
    analysis_target = st.selectbox(
        "Filière analysée",
        selected,
        index=selected.index(target) if target in selected else 0,
        key="impact_filiere_cible",
    )

# Référentiel projeté, utilisé une seule fois pour tous les calculs d'impact.
baseline_by_filiere = projected_result.set_index("Filière").to_dict("index")
impacts_by_filiere: dict[str, pd.DataFrame] = {}
impact_records: list[dict[str, Any]] = []

for mat in matieres:
    test_bac = dict(projected_bac)
    test_means = {m: dict(v) for m, v in projected_means.items()}
    test_means[mat]["tle"] = min(20.0, test_means[mat]["tle"] + 1.0)
    test_bac[mat] = min(20.0, test_bac[mat] + 1.0)

    _, test_scores_all = calculer_candidat(
        params,
        serie,
        mention,
        test_bac,
        test_means,
        selected,
    )
    test_eval = evaluer_scores(
        {f: test_scores_all[f] for f in selected if f in test_scores_all},
        serie,
    ).set_index("Filière")

    for filiere in selected:
        baseline = baseline_by_filiere.get(filiere)
        if baseline is None or filiere not in test_eval.index:
            continue
        tested = test_eval.loc[filiere]
        gain_score = float(tested["Score"] - baseline["Score"])
        gain_prob = (
            float(tested["Probabilité"] - baseline["Probabilité"])
            if not pd.isna(tested["Probabilité"]) and not pd.isna(baseline["Probabilité"])
            else np.nan
        )
        gain_rank = (
            float(baseline["Rang moyen"] - tested["Rang moyen"])
            if not pd.isna(tested["Rang moyen"]) and not pd.isna(baseline["Rang moyen"])
            else np.nan
        )
        impact_records.append({
            "Filière": filiere,
            "Matière": mat,
            "Gain score +1": gain_score,
            "Gain probabilité +1": gain_prob,
            "Gain de rang +1": gain_rank,
            "Améliorée": bool(
                (not pd.isna(gain_rank) and gain_rank > 0)
                or (not pd.isna(gain_prob) and gain_prob > 0)
                or gain_score > 0
            ),
        })

impact_detail = pd.DataFrame(impact_records)
for filiere in selected:
    detail = impact_detail[impact_detail["Filière"] == filiere].copy()
    detail = detail.sort_values(
        ["Gain de rang +1", "Gain probabilité +1", "Gain score +1"],
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)
    detail["Priorité"] = detail["Gain de rang +1"].fillna(0).rank(
        method="dense", ascending=False
    ).map(
        lambda rank: "🔥🔥🔥🔥🔥" if rank <= 1 else "🔥🔥🔥🔥" if rank <= 2 else "🔥🔥🔥" if rank <= 4 else "🔥🔥" if rank <= 6 else "🔥"
    )
    impacts_by_filiere[filiere] = detail

if impact_detail.empty:
    impact_global = pd.DataFrame()
else:
    impact_global = (
        impact_detail.groupby("Matière", as_index=False)
        .agg(
            **{
                "Gain score moyen": ("Gain score +1", "mean"),
                "Gain probabilité moyenne": ("Gain probabilité +1", "mean"),
                "Gain moyen de rang": ("Gain de rang +1", "mean"),
                "Nombre de filières améliorées": ("Améliorée", "sum"),
                "Nombre de filières analysées": ("Filière", "nunique"),
            }
        )
    )
    impact_global["Filières améliorées"] = (
        impact_global["Nombre de filières améliorées"].astype(int).astype(str)
        + "/"
        + impact_global["Nombre de filières analysées"].astype(int).astype(str)
    )
    impact_global = impact_global.sort_values(
        ["Gain moyen de rang", "Gain probabilité moyenne", "Gain score moyen"],
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)
    impact_global["Priorité"] = impact_global["Gain moyen de rang"].fillna(0).rank(
        method="dense", ascending=False
    ).map(
        lambda rank: "🔥🔥🔥🔥🔥" if rank <= 1 else "🔥🔥🔥🔥" if rank <= 2 else "🔥🔥🔥" if rank <= 4 else "🔥🔥" if rank <= 6 else "🔥"
    )

if analysis_mode == "Toutes les filières sélectionnées":
    st.dataframe(
        impact_global[[
            "Matière",
            "Gain score moyen",
            "Gain probabilité moyenne",
            "Gain moyen de rang",
            "Filières améliorées",
            "Priorité",
        ]],
        hide_index=True,
        width="stretch",
        column_config={
            "Gain score moyen": st.column_config.NumberColumn(format="%+.3f"),
            "Gain probabilité moyenne": st.column_config.NumberColumn(format="%+.2f pt"),
            "Gain moyen de rang": st.column_config.NumberColumn(format="%+.1f places"),
        },
    )
    if not impact_global.empty:
        best = impact_global.iloc[0]
        st.info(
            f"Globalement, **{best['Matière']}** est la matière la plus porteuse : "
            f"un point gagné améliore **{best['Filières améliorées']} filières** et fait gagner "
            f"en moyenne **{fmt(best['Gain moyen de rang'], 1)} places**."
        )
else:
    impact_selected = impacts_by_filiere.get(analysis_target, pd.DataFrame())
    st.dataframe(
        impact_selected[[
            "Matière",
            "Gain score +1",
            "Gain probabilité +1",
            "Gain de rang +1",
            "Priorité",
        ]],
        hide_index=True,
        width="stretch",
        column_config={
            "Gain score +1": st.column_config.NumberColumn(format="%+.3f"),
            "Gain probabilité +1": st.column_config.NumberColumn(format="%+.2f pt"),
            "Gain de rang +1": st.column_config.NumberColumn(format="%+.1f places"),
        },
    )
    if not impact_selected.empty:
        best = impact_selected.iloc[0]
        st.info(
            f"Pour **{analysis_target}**, la matière la plus sensible est **{best['Matière']}** : "
            f"un point gagné augmente le score d'environ **{fmt(best['Gain score +1'], 3)} point** "
            f"et fait gagner en moyenne **{fmt(best['Gain de rang +1'], 1)} places**."
        )

st.subheader("6. Scénarios et plan de progression")
scenario_rows = []
for name, factor in [("Prudent", 0.5), ("Réaliste", 1.0), ("Ambitieux", 1.5)]:
    # Les scénarios sont construits autour des notes réellement présentes dans
    # le tableau : 50 % du progrès, scénario saisi, puis 150 % du progrès.
    bac_s: dict[str, float] = {}
    means_s: dict[str, dict[str, float]] = {}
    for mat in matieres:
        means_s[mat] = {}
        for year in ("2nde", "1ere", "tle"):
            current_value = float(current_means[mat][year])
            projected_value = float(projected_means[mat][year])
            means_s[mat][year] = float(np.clip(
                current_value + factor * (projected_value - current_value), 0, 20
            ))
        bac_s[mat] = float(np.clip(
            current_bac[mat] + factor * (projected_bac[mat] - current_bac[mat]), 0, 20
        ))
    _, scores_s = calculer_candidat(params, serie, mention, bac_s, means_s, [target])
    ev = evaluer_scores(scores_s, serie)
    if not ev.empty:
        row = ev.iloc[0]
        scenario_rows.append({"Scénario": name, "Score": row["Score"], "Probabilité": row["Probabilité"], "Rang moyen": row["Rang moyen"]})
scenarios = pd.DataFrame(scenario_rows)
st.dataframe(
    scenarios,
    hide_index=True,
    width="stretch",
    column_config={
        "Score": st.column_config.NumberColumn(format="%.2f"),
        "Probabilité": st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=100),
        "Rang moyen": st.column_config.NumberColumn(format="%.0f"),
    },
)

plan_rows = []
for _, row in edited.iterrows():
    mat = str(row["Matière"])
    actual = float(row[years[-1]])
    plan_rows.append({
        "Matière": mat,
        "Niveau actuel": actual,
        "Objectif Terminale": projected_means[mat]["tle"],
        "Objectif Bac": projected_bac[mat],
        "Effort total": projected_bac[mat] - actual,
    })
plan = pd.DataFrame(plan_rows).sort_values("Effort total", ascending=False)
st.dataframe(
    plan,
    hide_index=True,
    width="stretch",
    column_config={
        "Niveau actuel": st.column_config.NumberColumn(format="%.2f"),
        "Objectif Terminale": st.column_config.NumberColumn(format="%.2f"),
        "Objectif Bac": st.column_config.NumberColumn(format="%.2f"),
        "Effort total": st.column_config.NumberColumn(format="%+.2f"),
    },
)

# Trajectoire complète : moyennes réellement saisies jusqu'au niveau actuel,
# puis moyennes projetées uniquement pour les étapes futures.
edited_by_subject = edited.set_index("Matière")
trajectory_data: dict[str, list[float] | list[str]] = {
    "Étape": ["2nde", "1ère", "Terminale", "Bac"]
}

for mat in matieres[:6]:
    if level == "Seconde":
        values = [
            float(edited_by_subject.loc[mat, "2nde"]),
            float(projected_means[mat]["1ere"]),
            float(projected_means[mat]["tle"]),
            float(projected_bac[mat]),
        ]
    elif level == "Première":
        values = [
            float(edited_by_subject.loc[mat, "2nde"]),
            float(edited_by_subject.loc[mat, "1ere"]),
            float(projected_means[mat]["tle"]),
            float(projected_bac[mat]),
        ]
    else:  # Terminale
        values = [
            float(edited_by_subject.loc[mat, "2nde"]),
            float(edited_by_subject.loc[mat, "1ere"]),
            float(edited_by_subject.loc[mat, "tle"]),
            float(projected_bac[mat]),
        ]

    trajectory_data[mat] = values

trajectory = pd.DataFrame(trajectory_data)
trajectory_long = trajectory.melt(
    id_vars="Étape",
    var_name="Matière",
    value_name="Note",
)

# Altair permet de verrouiller l'ordre chronologique de l'axe,
# contrairement à st.line_chart qui peut trier les libellés alphabétiquement.
import altair as alt

trajectory_chart = (
    alt.Chart(trajectory_long)
    .mark_line(point=True)
    .encode(
        x=alt.X(
            "Étape:N",
            sort=["2nde", "1ère", "Terminale", "Bac"],
            title="Étape",
        ),
        y=alt.Y(
            "Note:Q",
            title="Note /20",
            scale=alt.Scale(domain=[0, 20]),
        ),
        color=alt.Color("Matière:N", title="Matière"),
        tooltip=[
            alt.Tooltip("Étape:N", title="Étape"),
            alt.Tooltip("Matière:N", title="Matière"),
            alt.Tooltip("Note:Q", title="Note", format=".2f"),
        ],
    )
    .properties(height=360)
    .interactive()
)

st.altair_chart(trajectory_chart, width="stretch")

st.subheader("7. Rapport personnalisé")
st.caption("Le rapport reprend la synthèse, les matières prioritaires et le plan de progression. Il ne constitue pas une décision officielle.")
pdf = make_report_pdf(serie, level, target, comparison, impact_global, impacts_by_filiere, plan)
if pdf:
    st.download_button(
        "📄 Télécharger le rapport PDF",
        data=pdf,
        file_name=f"projection_inphb_{serie}_{target}.pdf".replace(" ", "_"),
        mime="application/pdf",
        type="primary",
    )
else:
    st.warning("La génération PDF nécessite la dépendance reportlab.")
