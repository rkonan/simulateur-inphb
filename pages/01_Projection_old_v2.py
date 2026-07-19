from __future__ import annotations

import io
import os
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
        projected_bac[mat] = float(np.clip(terminal_target + bac_bonus + 0.25 * adj, 0, 20))

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
level = profile_cols[0].selectbox("Ton niveau actuel", ["Seconde", "Première", "Terminale"])
serie = profile_cols[1].selectbox("Série de bac visée", sorted(params.coeffs_bac))
mention = profile_cols[2].selectbox("Mention cible au bac", params.mentions["mention"].astype(str).tolist(), index=min(2, len(params.mentions)-1))

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

target = st.selectbox("🎯 Filière objectif", calculable)
selected = st.multiselect("Filières à comparer", calculable, default=calculable)
if not selected:
    st.stop()
matieres = matieres_reelles_pour_formules(params, serie, calculable)

st.subheader("1. Situation scolaire actuelle")
years = completed_years(level)
initial_rows = []
for mat in matieres:
    row = {"Matière": mat, "2nde": 10.0, "1ere": 10.0, "tle": 10.0}
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
control_cols = st.columns(2)
global_progress = control_cols[0].slider("Progression jusqu'à la Terminale", -2.0, 5.0, 1.0, 0.25, help="Progression générale appliquée aux années scolaires encore à venir.")
bac_bonus = control_cols[1].slider("Écart Bac par rapport à la Terminale", -2.0, 3.0, 0.5, 0.25)

subject_adjustments: dict[str, float] = {}
with st.expander("🎛 Ajuster la progression matière par matière", expanded=True):
    st.caption("Ces curseurs s'ajoutent à la progression générale. Ils permettent de tester un effort ciblé.")
    columns = st.columns(2)
    for index, mat in enumerate(matieres):
        subject_adjustments[mat] = columns[index % 2].slider(
            mat,
            -3.0,
            5.0,
            0.0,
            0.25,
            key=f"evolution_{serie}_{slug(mat)}",
        )

current_bac, current_means, projected_bac, projected_means = build_profiles(
    edited, level, global_progress, bac_bonus, subject_adjustments
)

st.subheader("3. Projection globale des notes")
st.caption(
    "Ce tableau récapitule les notes déjà saisies et les notes projetées après application "
    "de la progression générale, des ajustements par matière et de l'hypothèse Bac."
)

projection_rows: list[dict[str, Any]] = []
for _, row in edited.iterrows():
    mat = str(row["Matière"])
    note_actuelle = float(row[years[-1]])
    projection_rows.append(
        {
            "Matière": mat,
            "Note actuelle": note_actuelle,
            "Progression générale": 0.0 if level == "Terminale" else float(global_progress),
            "Ajustement matière": float(subject_adjustments.get(mat, 0.0)),
            "2nde": float(projected_means[mat]["2nde"]),
            "1ère": float(projected_means[mat]["1ere"]),
            "Terminale projetée": float(projected_means[mat]["tle"]),
            "Bac projeté": float(projected_bac[mat]),
            "Évolution finale": float(projected_bac[mat] - note_actuelle),
        }
    )

projection_notes = pd.DataFrame(projection_rows)

# Les colonnes déjà passées restent visibles, mais on masque les étapes inutiles
# afin de garder un tableau lisible selon le niveau de l'élève.
if level == "Seconde":
    projection_columns = [
        "Matière", "Note actuelle", "Progression générale", "Ajustement matière",
        "1ère", "Terminale projetée", "Bac projeté", "Évolution finale",
    ]
elif level == "Première":
    projection_columns = [
        "Matière", "Note actuelle", "Progression générale", "Ajustement matière",
        "Terminale projetée", "Bac projeté", "Évolution finale",
    ]
else:
    projection_columns = [
        "Matière", "Note actuelle", "Ajustement matière",
        "Terminale projetée", "Bac projeté", "Évolution finale",
    ]

st.dataframe(
    projection_notes[projection_columns],
    hide_index=True,
    width="stretch",
    column_config={
        "Matière": st.column_config.TextColumn("Matière"),
        "Note actuelle": st.column_config.NumberColumn(format="%.2f"),
        "Progression générale": st.column_config.NumberColumn(format="%+.2f"),
        "Ajustement matière": st.column_config.NumberColumn(format="%+.2f"),
        "1ère": st.column_config.NumberColumn("Première projetée", format="%.2f"),
        "Terminale projetée": st.column_config.NumberColumn(format="%.2f"),
        "Bac projeté": st.column_config.NumberColumn(format="%.2f"),
        "Évolution finale": st.column_config.NumberColumn(format="%+.2f"),
    },
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
    adj = {m: value * factor for m, value in subject_adjustments.items()}
    _, _, bac_s, means_s = build_profiles(edited, level, global_progress * factor, bac_bonus * factor, adj)
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

trajectory = pd.DataFrame({
    "Étape": ["2nde", "1ère", "Terminale", "Bac"],
    **{
        mat: [projected_means[mat]["2nde"], projected_means[mat]["1ere"], projected_means[mat]["tle"], projected_bac[mat]]
        for mat in matieres[:6]
    },
}).set_index("Étape")
st.line_chart(trajectory, y_label="Note projetée /20")

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
