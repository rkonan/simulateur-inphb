from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from streamlit_js_eval import streamlit_js_eval
from simulateur_core import (
    calculer_candidat,
    charger_parametres,
    evaluer_admissibilite_depuis_db,
    filieres_autorisees_serie,
    lignes_formule,
    lister_filieres_db,
    sauvegarder_simulation_anonyme,
    slug,
    stats_collecte,
)

st.set_page_config(
    page_title="Simulateur INP-HB",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        display: none;
    }

    [data-testid="collapsedControl"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
LOCAL_PARAMS = Path(os.getenv("INPHB_PARAMS", "parametres_simulateur_inphb.xlsx"))
LOCAL_DB = Path(os.getenv("INPHB_DISTRIBUTIONS_DB", "population_inphb_distributions.db"))
COLLECTE_DB = Path(os.getenv("INPHB_COLLECTE_DB", "data/simulations_anonymes.db"))
SEUIL_ADMISSIBLES = 2000


def formater_probabilite(valeur: float | None) -> str:
    if valeur is None or pd.isna(valeur):
        return "Non calculée"
    valeur = float(valeur)
    if valeur == 0:
        return "0 %"
    if valeur < 0.000001:
        return "< 0,000001 %"
    if valeur < 0.01:
        return f"{valeur:.6f} %".replace(".", ",")
    if valeur < 1:
        return f"{valeur:.3f} %".replace(".", ",")
    return f"{valeur:.1f} %".replace(".", ",")


def niveau_probabilite(valeur: float | None) -> tuple[str, str, str]:
    if valeur is None or pd.isna(valeur):
        return (
            "Non calculé",
            "☆☆☆☆☆",
            "Impossible d'estimer tes chances avec les informations disponibles."
        )

    valeur = float(valeur)

    if valeur >= 80:
        return (
            "Très favorable",
            "★★★★★",
            "Ton dossier semble particulièrement bien adapté à cette filière. D'après la population de référence, tu fais partie des profils les plus compétitifs."
        )

    if valeur >= 60:
        return (
            "Favorable",
            "★★★★☆",
            "Ton dossier présente de bons atouts pour cette filière. Tes chances d'être admissible sont encourageantes."
        )

    if valeur >= 40:
        return (
            "À confirmer",
            "★★★☆☆",
            "Ton dossier est proche du niveau généralement attendu. Le résultat dépendra aussi du niveau des autres candidats cette année."
        )

    if valeur >= 20:
        return (
            "À renforcer",
            "★★☆☆☆",
            "Ton dossier paraît un peu en dessous du niveau habituellement observé. Il reste intéressant de candidater si cette filière t'intéresse."
        )

    return (
        "Sélectif",
        "★☆☆☆☆",
        "Cette filière semble très sélective pour ton profil. Tu peux néanmoins la conserver parmi tes choix et compléter ta candidature avec des filières où ton dossier est plus compétitif."
    )


def formater_marge(valeur: float | None) -> str:
    if valeur is None or pd.isna(valeur):
        return "Non calculée"
    valeur = float(valeur)
    signe = "+" if valeur >= 0 else ""
    return f"{signe}{valeur:.0f} places"


def niveau_marge(valeur: float | None) -> str:
    if valeur is None or pd.isna(valeur):
        return "Non calculée"
    valeur = float(valeur)
    if valeur >= 150:
        return "Large marge"
    if valeur >= 50:
        return "Marge favorable"
    if valeur >= -50:
        return "Zone limite"
    if valeur >= -150:
        return "Marge défavorable"
    return "Écart important"


def score_contributions(
    params: Any,
    filiere: str,
    serie: str,
    calculs: pd.DataFrame,
) -> tuple[pd.DataFrame, float]:
    formule = lignes_formule(params, filiere, serie).copy()
    mgm_par_matiere = dict(zip(calculs["Matière"], calculs["MGM"]))
    denominateur = float(formule["denominateur"].iloc[0])

    lignes = []
    for _, ligne in formule.iterrows():
        matiere = str(ligne["matiere"])
        coefficient = float(ligne["coefficient_dossier"])
        mgm = float(mgm_par_matiere[matiere])
        contribution_brute = mgm * coefficient
        contribution_score = contribution_brute / denominateur
        lignes.append(
            {
                "Matière": matiere,
                "MGM": round(mgm, 4),
                "Coefficient": coefficient,
                "Contribution pondérée": round(contribution_brute, 4),
                "Contribution au score": round(contribution_score, 4),
            }
        )

    detail = pd.DataFrame(lignes)
    score = float(detail["Contribution au score"].sum())
    return detail, round(score, 4)


def recommandation_intrinseque(scores_tries: pd.DataFrame) -> str:
    if scores_tries.empty:
        return "Aucune recommandation disponible."

    top = scores_tries.head(min(3, len(scores_tries)))
    noms = top["Filière"].tolist()
    meilleur = noms[0]

    if len(noms) == 1:
        return (
            f"Ton dossier obtient son meilleur score sur **{meilleur}**. "
            "Cette première lecture porte uniquement sur tes notes et les coefficients de la filière."
        )

    liste = ", ".join(f"**{nom}**" for nom in noms[:-1]) + f" et **{noms[-1]}**"
    ecart = float(top.iloc[0]["Score dossier"] - top.iloc[-1]["Score dossier"])

    if ecart < 0.5:
        nuance = "Les scores sont proches, garde plusieurs choix ouverts."
    elif ecart < 1.5:
        nuance = "Le premier choix ressort, mais les deux suivants restent cohérents."
    else:
        nuance = "Le premier choix ressort nettement selon la structure de ton dossier."

    return (
        f"Selon tes notes seules, les filières les plus adaptées sont {liste}. "
        f"{nuance} Cette recommandation ne tient pas encore compte du niveau des autres candidats."
    )



def saisir_notes_desktop(
    matieres: list[str],
    serie: str,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Affiche la grille large utilisée sur ordinateur."""
    notes_bac: dict[str, float] = {}
    moyennes: dict[str, dict[str, float]] = {}

    headers = st.columns([2, 1, 1, 1, 1])
    for colonne, entete in zip(
        headers,
        ["**Matière**", "**2nde**", "**1ère**", "**Terminale**", "**Bac**"],
    ):
        colonne.markdown(entete)

    for index, matiere in enumerate(matieres):
        colonnes = st.columns([2, 1, 1, 1, 1])
        colonnes[0].write(matiere)

        key = f"{serie}_{slug(matiere)}_{index}"

        seconde = colonnes[1].number_input(
            "2nde",
            min_value=0.0,
            max_value=20.0,
            value=10.0,
            step=0.25,
            key=key + "s",
            label_visibility="collapsed",
        )
        premiere = colonnes[2].number_input(
            "1ère",
            min_value=0.0,
            max_value=20.0,
            value=10.0,
            step=0.25,
            key=key + "p",
            label_visibility="collapsed",
        )
        terminale = colonnes[3].number_input(
            "Terminale",
            min_value=0.0,
            max_value=20.0,
            value=10.0,
            step=0.25,
            key=key + "t",
            label_visibility="collapsed",
        )
        bac = colonnes[4].number_input(
            "Bac",
            min_value=0.0,
            max_value=20.0,
            value=10.0,
            step=0.25,
            key=key + "b",
            label_visibility="collapsed",
        )

        notes_bac[matiere] = float(bac)
        moyennes[matiere] = {
            "2nde": float(seconde),
            "1ere": float(premiere),
            "tle": float(terminale),
        }

    return notes_bac, moyennes


def saisir_notes_mobile(
    matieres: list[str],
    serie: str,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Affiche une carte compacte et tactile pour chaque matière."""
    notes_bac: dict[str, float] = {}
    moyennes: dict[str, dict[str, float]] = {}

    st.caption(
        "Saisis les quatre notes de chaque matière. "
        "Tu peux passer rapidement d'un champ au suivant avec le clavier du téléphone."
    )

    for index, matiere in enumerate(matieres):
        key = f"{serie}_{slug(matiere)}_{index}"

        with st.container(border=True):
            st.markdown(f"#### {matiere}")

            ligne_1 = st.columns(2, gap="small")
            seconde = ligne_1[0].number_input(
                "2nde",
                min_value=0.0,
                max_value=20.0,
                value=10.0,
                step=0.25,
                key=key + "s",
            )
            premiere = ligne_1[1].number_input(
                "1ère",
                min_value=0.0,
                max_value=20.0,
                value=10.0,
                step=0.25,
                key=key + "p",
            )

            ligne_2 = st.columns(2, gap="small")
            terminale = ligne_2[0].number_input(
                "Terminale",
                min_value=0.0,
                max_value=20.0,
                value=10.0,
                step=0.25,
                key=key + "t",
            )
            bac = ligne_2[1].number_input(
                "Bac",
                min_value=0.0,
                max_value=20.0,
                value=10.0,
                step=0.25,
                key=key + "b",
            )

        notes_bac[matiere] = float(bac)
        moyennes[matiere] = {
            "2nde": float(seconde),
            "1ere": float(premiere),
            "tle": float(terminale),
        }

    return notes_bac, moyennes


st.title("🎓 Simulateur de dossier bachelier INP-HB")

st.markdown(
    """
<h4 style="
    color:#4b5563;
    font-weight:500;
    margin-top:-0.5rem;
    margin-bottom:1rem;
">
📊 Évalue ton dossier et identifie les filières où il paraît le plus compétitif.
</h4>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div style="
    background:#f6f8fa;
    border-left:4px solid #6c757d;
    padding:0.9rem 1rem;
    border-radius:0.35rem;
    margin:0.8rem 0 1.4rem 0;
    font-size:0.95rem;
    line-height:1.5;
    color:#343a40;
">
Le simulateur calcule le score de ton dossier selon les règles propres à chaque filière, puis le compare à une population de référence issue d'un modèle statistique.
<br>
<strong>Les résultats sont indicatifs. Ils ne garantissent pas l'admissibilité et ne remplacent pas une décision officielle de l'INP-HB.</strong>
</div>
""",
    unsafe_allow_html=True,
)

largeur = streamlit_js_eval(
    js_expressions="window.innerWidth",
    key="viewport_width_js",
)

if largeur is None:
    st.info("Adaptation de l'affichage à ton écran…")
    st.stop()

EST_MOBILE = int(largeur) < 768


nb_dossiers=3000
collecte=True

if not LOCAL_PARAMS.exists() or not LOCAL_DB.exists():
    st.error("Ajoute parametres_simulateur_inphb.xlsx et population_inphb_distributions.db dans le dossier.")
    st.stop()

params = charger_parametres(LOCAL_PARAMS)
filieres_db = lister_filieres_db(LOCAL_DB)

st.subheader("1. Ton profil")
col1, col2 = st.columns(2)
serie = col1.selectbox("Série du bac", sorted(params.coeffs_bac))
mention = col2.selectbox(
    "Mention obtenue",
    params.mentions["mention"].astype(str).tolist(),
    index=1,
)

compatibles = filieres_autorisees_serie(params, serie, filieres_db)
st.subheader("2. Tes filières compatibles")
filieres = st.multiselect(
    "Toutes les filières compatibles sont sélectionnées par défaut",
    compatibles,
    default=compatibles,
)
if not filieres:
    st.warning("Sélectionne au moins une filière.")
    st.stop()

matieres = set()
for filiere in filieres:
    matieres.update(
        lignes_formule(params, filiere, serie)["matiere"].tolist()
    )
matieres = sorted(matieres)

st.subheader("📚 3. Saisie des notes")

if EST_MOBILE:
    notes_bac, moyennes = saisir_notes_mobile(
        matieres=matieres,
        serie=serie,
    )
else:
    notes_bac, moyennes = saisir_notes_desktop(
        matieres=matieres,
        serie=serie,
    )

calculs, scores = calculer_candidat(params, serie, mention, notes_bac, moyennes, filieres)
st.subheader("🧮 4. Calcul des moyennes par matière")
#st.subheader("4. Vérification des calculs MC et MGM")
st.dataframe(
    calculs,
    width="stretch",
    hide_index=True,
    column_config={
        "2nde": st.column_config.NumberColumn(format="%.2f"),
        "1ère": st.column_config.NumberColumn(format="%.2f"),
        "Terminale": st.column_config.NumberColumn(format="%.2f"),
        "Bac": st.column_config.NumberColumn(format="%.2f"),
        "MC": st.column_config.NumberColumn(format="%.2f"),
        "MGM": st.column_config.NumberColumn(format="%.2f"),
    },
)
with st.expander("Justificatif des formules MC et MGM"):
    st.markdown("**Étape 1, moyenne de classe par matière**")
    st.latex(r"MC=\frac{2\times M_{2nde}+3\times M_{1ère}+5\times M_{Terminale}}{10}")
    st.markdown("**Étape 2, moyenne générale par matière**")
    st.latex(r"MGM=\frac{MC+3\times Note_{Bac}}{4}")
    st.info("La MGM, et non la note du bac seule, est ensuite pondérée selon la filière.")

# -----------------------------------------------------------------------------
# Analyse intrinsèque des scores, avant toute comparaison à la population
# -----------------------------------------------------------------------------
st.subheader("🏆 5. Tes scores dossier par filière")
score_rows = [
    {"Filière": filiere, "Score dossier": float(score)}
    for filiere, score in scores.items()
]
scores_tries = (
    pd.DataFrame(score_rows)
    .sort_values("Score dossier", ascending=False)
    .reset_index(drop=True)
)
if not scores_tries.empty:
    scores_tries.insert(0, "Rang du score", np.arange(1, len(scores_tries) + 1))


#st.caption(
st.success(
    "💡  Ce premier classement compare uniquement tes propres scores entre filières. "
    "Il ne mesure pas encore ta position face aux autres candidats."
)
st.dataframe(
    scores_tries,
    width="stretch",
    hide_index=True,
    column_config={
        "Rang du score": st.column_config.NumberColumn(format="%d"),
        "Score dossier": st.column_config.NumberColumn(format="%.2f"),
    },
)

with st.expander("Voir le détail du calcul des scores par filière"):
    filiere_detail = st.selectbox(
        "Filière à détailler",
        scores_tries["Filière"].tolist(),
        key="filiere_detail_score",
    )
    detail, score_recalcule = score_contributions(params, filiere_detail, serie, calculs)
    st.dataframe(
        detail,
        width="stretch",
        hide_index=True,
        column_config={
            "MGM": st.column_config.NumberColumn(format="%.2f"),
            "Coefficient": st.column_config.NumberColumn(format="%.0f"),
            "Contribution pondérée": st.column_config.NumberColumn(format="%.4f"),
            "Contribution au score": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    denominateur = float(lignes_formule(params, filiere_detail, serie)["denominateur"].iloc[0])
    st.latex(
        rf"Score_{{{filiere_detail}}}=\frac{{\sum(MGM_{{matière}}\times coefficient)}}{{{denominateur:g}}}"
    )
    st.metric(f"Score dossier {filiere_detail}", f"{score_recalcule:.2f} / 20")

st.markdown("### 🎯 Première recommandation")
st.info(recommandation_intrinseque(scores_tries))
if not scores_tries.empty:
    top_intrinseque = scores_tries.head(min(3, len(scores_tries)))
    cols_top = st.columns(len(top_intrinseque))
    for rang, (colonne, ligne) in enumerate(zip(cols_top, top_intrinseque.to_dict("records")), start=1):
        with colonne:
            with st.container(border=True):
                st.caption(f"#{rang} selon ton score")
                st.markdown(f"### {ligne['Filière']}")
                st.metric("Score dossier", f"{ligne['Score dossier']:.2f} / 20")

#st.subheader("6. Données anonymisées")
#consentement = st.checkbox(
#    "J’accepte l’enregistrement anonymisé de mes notes pour améliorer le modèle.",
#    False,
#)
consentement=True
st.subheader("6. Comparaison avec les autres candidats")
st.caption(
    "Cette seconde analyse positionne chaque score dans une cohorte fictive et estime "
    f"la chance d'entrer dans les {SEUIL_ADMISSIBLES} premiers dossiers."
)

if st.button("Analyser mes chances d'admissibilité", type="primary", width="stretch"):
    if int(nb_dossiers) < SEUIL_ADMISSIBLES:
        st.error(f"Le nombre de dossiers concurrents doit être au moins égal à {SEUIL_ADMISSIBLES}.")
        st.stop()

    resultats: list[dict[str, Any]] = []
    progression = st.progress(0, text="Préparation de l’analyse…")
    total_filieres = max(len(scores), 1)

    for index, (filiere, score) in enumerate(scores.items(), start=1):
        progression.progress((index - 1) / total_filieres, text=f"Analyse de {filiere}…")
        resultat = evaluer_admissibilite_depuis_db(
            path=LOCAL_DB,
            filiere=filiere,
            score_candidat=score,
            nb_dossiers_concurrents=int(nb_dossiers),
            seuil_admissibles=SEUIL_ADMISSIBLES,
        )
        marge = None
        if resultat.get("rang_moyen") is not None:
            marge = SEUIL_ADMISSIBLES - float(resultat["rang_moyen"])
        resultats.append({"filiere": filiere, "score": score, "marge": marge, **resultat})
        progression.progress(index / total_filieres, text=f"{index}/{total_filieres} filières analysées")

    progression.empty()
    # frame = pd.DataFrame(resultats).sort_values(
    #     "probabilite", ascending=False, na_position="last"
    # )
    frame = pd.DataFrame(resultats).sort_values(
        "marge", ascending=False, na_position="last"
    )
    frame_calcule = frame[frame["probabilite"].notna()].copy()

    if frame_calcule.empty:
        st.warning(
            "Aucune probabilité n'a pu être calculée. Vérifie la base de population "
            "et les paramètres du modèle."
        )
    else:


        meilleure = frame_calcule.iloc[0]
        st.success(
            f"Meilleure chance estimée : **{meilleure['filiere']}**"
            # f"Meilleure chance estimée : **{meilleure['filiere']}** avec "
            # f"**{formater_probabilite(meilleure['probabilite'])}**."
        )

        resume_1,  resume_3, resume_4 = st.columns([1.2,  1, 1])
        resume_1.metric("Meilleure filière", str(meilleure["filiere"]))
        # resume_2.metric("Probabilité estimée", formater_probabilite(meilleure["probabilite"]))
        resume_3.metric(
            "Rang moyen projeté",
            f"{meilleure['rang_moyen']:.0f} / {int(nb_dossiers):,}".replace(",", " "),
        )
        resume_4.metric(
            "Marge au seuil",
            formater_marge(meilleure.get("marge")),
            help="Seuil admissible moins rang moyen. Une marge positive place le dossier au-dessus du seuil.",
        )

        st.subheader("Top 3 des filières après comparaison")
        top3 = frame_calcule.head(3).reset_index(drop=True)
        cartes = st.columns(len(top3))

        for rang, (colonne, ligne) in enumerate(zip(cartes, top3.to_dict("records")), start=1):
            niveau, etoiles,commentaire = niveau_probabilite(ligne.get("probabilite"))
            with colonne:
                with st.container(border=True):
                    st.caption(f"#{rang}")
                    st.markdown(f"### {ligne['filiere']}")
                    st.write(etoiles)
                    st.markdown(f"**{niveau}**")
                    st.markdown(f"**{commentaire}**")
                    # st.markdown(f"## {formater_probabilite(ligne.get('probabilite'))}")
                    
                    if pd.notna(ligne.get("rang_moyen")):
                        st.caption(
                            f"Rang moyen : {ligne['rang_moyen']:.0f} / {int(nb_dossiers):,}".replace(",", " ")
                        )
                    st.caption(
                        f"Marge : {formater_marge(ligne.get('marge'))} · {niveau_marge(ligne.get('marge'))}"
                    )
                    # if pd.notna(ligne.get("rang_p90")):
                    #     st.caption(f"Rang prudent P90 : {ligne['rang_p90']:.0f}")
                    # if pd.notna(ligne.get("percentile")):
                    #     st.caption(f"Percentile du dossier : {ligne['percentile']:.1f} %")

        st.markdown("### Lecture rapide")
        proba = float(meilleure["probabilite"])
        marge = meilleure.get("marge")
        if proba >= 80:
            lecture = (
                f"Ton dossier est particulièrement bien adapté à la filière **{meilleure['filiere']}**. "
                f"La marge moyenne au seuil est de **{formater_marge(marge)}**."
            )
        elif proba >= 60:
            lecture = (
                f"Ton dossier est compétitif pour **{meilleure['filiere']}**. "
                f"La marge moyenne est de **{formater_marge(marge)}**, sans garantie d'admissibilité."
            )
        elif proba >= 40:
            lecture = (
                f"Ton dossier reste dans la course pour **{meilleure['filiere']}**. "
                f"Il se situe dans une zone sensible autour du seuil, avec une marge de **{formater_marge(marge)}**."
            )
        elif proba >= 20:
            lecture = (
                f"Ton dossier est proche de la zone d'admissibilité pour **{meilleure['filiere']}**. "
                f"La marge moyenne est de **{formater_marge(marge)}**."
            )
        else:
            lecture = (
                f"Ton meilleur positionnement comparatif est **{meilleure['filiere']}**, "
                f"mais la marge moyenne reste de **{formater_marge(marge)}**."
            )
        st.info(lecture)


    st.subheader("📋 Classement complet des filières")
    tableau_synthese = frame_calcule.copy()

    # tableau_synthese["Marge"] = (
    #     tableau_synthese["seuil_admissible"]
    #     - tableau_synthese["rang_moyen"]
    # )
    tableau_synthese["Marge"]=tableau_synthese["marge"]
    tableau_synthese["Marge au seuil"] = tableau_synthese["marge"].apply(
        lambda x: (
            f"+{x:.0f} places"
            if pd.notna(x) and x >= 0
            else f"{x:.0f} places"
            if pd.notna(x)
            else "Non calculée"
        )
    )

    tableau_synthese["Rang moyen"] = tableau_synthese["rang_moyen"].apply(
        lambda x: (
            f"{x:.0f} / {nb_dossiers:,}".replace(",", " ")
            if pd.notna(x)
            else "Non calculé"
        )
    )

    tableau_synthese["Score dossier"] = tableau_synthese[
        "score"
    ].apply(
        lambda x: f"{x:.2f} / 20" if pd.notna(x) else "Non calculé"
    )

    tableau_synthese = tableau_synthese.sort_values(
        by=["Marge", "score"],
        ascending=[False, False],
    )

    tableau_synthese.insert(
        0,
        "Classement",
        range(1, len(tableau_synthese) + 1),
    )

    def statut_marge(marge: float | None) -> str:
        if marge is None or pd.isna(marge):
            return "Non calculé"

        if marge >= 150:
            return "🟢 Très favorable"

        if marge >= 50:
            return "🟢 Favorable"

        if marge >= -50:
            return "🟡 Zone limite"

        if marge >= -150:
            return "🟠 À renforcer"

        return "🔴 Sélectif"

    tableau_synthese["Position"] = tableau_synthese[
    "Marge"].apply(statut_marge)

    tableau_synthese = tableau_synthese[
        [
            "Classement",
            "filiere",
            "Score dossier",
            "Rang moyen",
            "Marge au seuil",
             "Position",
        ]
    ].rename(
        columns={
            "filiere": "Filière",
        }
    )

    

    st.dataframe(
        tableau_synthese,
        width="stretch",
        hide_index=True,
    )

    display = frame.copy()
    display["Probabilité estimée"] = display["probabilite_exacte"].map(formater_probabilite)
    display["Marge au seuil"] = display["marge"].map(formater_marge)
    display["Lecture de la marge"] = display["marge"].map(niveau_marge)

    display = display.rename(
        columns={
            "filiere": "Filière",
            "score": "Score dossier",
            "dossiers_concurrents": "Dossiers concurrents",
            "seuil_admissibles": "Seuil admissible",
            "rang_moyen": "Rang moyen",
            "rang_median": "Rang médian",
            "rang_p10": "Rang P10",
            "rang_p90": "Rang P90",
            "percentile": "Percentile base",
            "population": "Population de référence",
        }
    )

    with st.expander("📊 Tableau détaillé des probabilités"):
        st.dataframe(
            display[
                [
                    "Filière",
                    "Score dossier",
                    "Dossiers concurrents",
                    "Seuil admissible",
                    "Marge au seuil",
                    "Lecture de la marge",
                    "Probabilité estimée",
                    "Rang moyen",
                    "Rang médian",
                    "Rang P10",
                    "Rang P90",
                    "Percentile base",
                    "Population de référence",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

        with st.expander("Comment lire l'analyse comparative ?"):
            st.markdown(
                f"""
    - La base synthétique contient la distribution des scores par filière, arrondis à deux décimales.
    - La proportion de profils ayant un score supérieur au tien est calculée directement dans SQLite.
    - Cette proportion sert à projeter ton rang parmi **{int(nb_dossiers):,} dossiers concurrents**.
    - Ton dossier est déclaré admissible lorsque son rang est dans le **Top {SEUIL_ADMISSIBLES}**.
    - La **marge au seuil** vaut : `{SEUIL_ADMISSIBLES} - rang moyen`.
    - Une marge positive place le rang moyen dans la zone admissible.
    - Une marge négative indique le nombre moyen de places manquantes.
    - La probabilité et les quantiles de rang viennent d'un calcul binomial exact.
    - Aucun tirage Monte-Carlo n'est effectué.
    """.replace(",", " ")
            )

    if collecte and consentement:
        sauvegarder_simulation_anonyme(
            COLLECTE_DB,
            serie,
            mention,
            notes_bac,
            moyennes,
            resultats,
            version_modele="v5_distribution",
        )
        #st.info("Profil enregistré sous forme anonymisée et dédupliquée par SHA-256.")
