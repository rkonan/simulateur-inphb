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
    current: pd.DataFrame,
    projected: pd.DataFrame,
    impact: pd.DataFrame,
    plan: pd.DataFrame,
) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except ImportError:
        return b""

    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=1.4*cm, leftMargin=1.4*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Rapport de projection INP-HB", styles["Title"]),
        Paragraph(f"Série : {serie} — Niveau : {level} — Objectif : {target}", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("Synthèse des filières", styles["Heading2"]),
    ]
    merged = current[["Filière", "Score", "Probabilité"]].merge(
        projected[["Filière", "Score", "Probabilité"]], on="Filière", suffixes=(" actuel", " projeté")
    ).head(12)
    data = [["Filière", "Score actuel", "Score projeté", "Prob. actuelle", "Prob. projetée"]]
    for _, row in merged.iterrows():
        data.append([
            row["Filière"], fmt(row["Score actuel"], 2), fmt(row["Score projeté"], 2),
            fmt(row["Probabilité actuel"], 1) + " %", fmt(row["Probabilité projeté"], 1) + " %",
        ])
    table = Table(data, repeatRows=1, colWidths=[4.3*cm, 2.4*cm, 2.4*cm, 2.7*cm, 2.7*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2F6FAB")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.extend([table, Spacer(1, 12), Paragraph("Matières prioritaires", styles["Heading2"])])
    impact_data = [["Matière", "Gain de score pour +1", "Gain de probabilité"]]
    for _, row in impact.head(8).iterrows():
        impact_data.append([row["Matière"], fmt(row["Gain score +1"], 3), fmt(row["Gain probabilité +1"], 2) + " pt"])
    impact_table = Table(impact_data, repeatRows=1, colWidths=[6*cm, 4*cm, 4*cm])
    impact_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EAF1F8")),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 8),
    ]))
    story.extend([impact_table, Spacer(1, 12), Paragraph("Plan de progression", styles["Heading2"])])
    for _, row in plan.head(10).iterrows():
        story.append(Paragraph(
            f"<b>{row['Matière']}</b> : {fmt(row['Niveau actuel'],1)} → {fmt(row['Objectif Terminale'],1)} → {fmt(row['Objectif Bac'],1)}",
            styles["Normal"],
        ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Résultats indicatifs, sans valeur officielle et dépendants des hypothèses de projection.", styles["Italic"]))
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

st.subheader("3. Impact sur l'admissibilité")
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

st.subheader("4. Matières à plus fort impact")
impact_rows = []
baseline_target = projected_scores_all.get(target)
baseline_eval = evaluer_scores({target: baseline_target}, serie) if baseline_target is not None else pd.DataFrame()
baseline_prob = float(baseline_eval.iloc[0]["Probabilité"]) if not baseline_eval.empty else np.nan
for mat in matieres:
    test_bac = dict(projected_bac)
    test_means = {m: dict(v) for m, v in projected_means.items()}
    test_means[mat]["tle"] = min(20.0, test_means[mat]["tle"] + 1.0)
    test_bac[mat] = min(20.0, test_bac[mat] + 1.0)
    _, test_scores = calculer_candidat(params, serie, mention, test_bac, test_means, [target])
    new_score = test_scores.get(target, baseline_target)
    test_eval = evaluer_scores({target: new_score}, serie)
    new_prob = float(test_eval.iloc[0]["Probabilité"]) if not test_eval.empty else np.nan
    impact_rows.append({
        "Matière": mat,
        "Gain score +1": float(new_score - baseline_target) if baseline_target is not None and new_score is not None else np.nan,
        "Gain probabilité +1": new_prob - baseline_prob if not pd.isna(new_prob) and not pd.isna(baseline_prob) else np.nan,
    })
impact = pd.DataFrame(impact_rows).sort_values(["Gain probabilité +1", "Gain score +1"], ascending=False, na_position="last")
impact["Priorité"] = impact["Gain score +1"].rank(method="dense", ascending=False).map(
    lambda rank: "🔥🔥🔥🔥🔥" if rank <= 1 else "🔥🔥🔥🔥" if rank <= 2 else "🔥🔥🔥" if rank <= 4 else "🔥🔥" if rank <= 6 else "🔥"
)
st.dataframe(
    impact,
    hide_index=True,
    width="stretch",
    column_config={
        "Gain score +1": st.column_config.NumberColumn(format="%+.3f"),
        "Gain probabilité +1": st.column_config.NumberColumn(format="%+.2f pt"),
    },
)

if not impact.empty:
    best = impact.iloc[0]
    st.info(
        f"Pour **{target}**, la matière actuellement la plus sensible est **{best['Matière']}** : "
        f"un gain simultané d'un point en Terminale et au Bac augmente le score d'environ "
        f"**{fmt(best['Gain score +1'], 3)} point**."
    )

st.subheader("5. Scénarios et plan de progression")
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

st.subheader("6. Rapport personnalisé")
st.caption("Le rapport reprend la synthèse, les matières prioritaires et le plan de progression. Il ne constitue pas une décision officielle.")
pdf = make_report_pdf(serie, level, target, current_result, projected_result, impact, plan)
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
