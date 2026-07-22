
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Tuple
from pathlib import Path
import pandas as pd

import streamlit as st



ALIASES_MATIERES = {
    "Mathématiques": "Maths", "Mathematiques": "Maths", "Maths": "Maths",
    "Sciences Physiques": "SP", "Sciences physiques": "SP", "SP": "SP",
    "SVT": "SVT", "Sciences de la Vie et de La Terre": "SVT",
    "Français": "Français", "Francais": "Français",
    "Anglais": "Anglais", "Langue": "Anglais", "LV1": "Anglais", "LV2": "LV2",
    "ScEco": "ScEco", "Sciences Economiques et Sociales": "ScEco",
    "MT": "MT", "Matière Technique": "MT",
    "Philosophie": "Philosophie", "Histoire-Géo": "Histoire-Géo",
}

@dataclass
class SimParams:
    fichier_excel: Path
    coeffs_bac: Dict[str, Dict[str, float]]
    groupes_score_bac: Dict[str, Dict[str, Tuple[Tuple[str, float], ...]]]
    coeffs_inphb: pd.DataFrame
    places: pd.DataFrame
    mentions: pd.DataFrame
    stats_series: pd.DataFrame
    eligibilite: pd.DataFrame
    liens_filieres: pd.DataFrame


@st.cache_data(show_spinner=False)
def charger_parametres(fichier_excel: str | Path) -> SimParams:
    path = Path(fichier_excel)
    bac = pd.read_excel(path, sheet_name="coefficients_bac").dropna(
        subset=["serie", "matiere", "coefficient"]
    )
    coeffs_bac: Dict[str, Dict[str, float]] = {}

    for _, row in bac.iterrows():
        serie = str(row["serie"]).strip()
        matiere = canon(row["matiere"])
        coefficient = float(row["coefficient"])

        if matiere in coeffs_bac.setdefault(serie, {}):
            raise ValueError(
                f"Matière BAC dupliquée après normalisation : {serie} / {matiere}."
            )
        coeffs_bac[serie][matiere] = coefficient

    groupes_df = pd.read_excel(path, sheet_name="groupes_matieres").dropna(
        subset=["serie", "matiere_bac", "groupe_inphb"]
    )
    colonnes_requises = {"serie", "matiere_bac", "groupe_inphb", "poids"}
    manquantes = colonnes_requises.difference(groupes_df.columns)
    if manquantes:
        raise ValueError(
            "Colonnes manquantes dans groupes_matieres : "
            + ", ".join(sorted(manquantes))
        )

    groupes_temp: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    for _, row in groupes_df.iterrows():
        serie = str(row["serie"]).strip()
        matiere = canon(row["matiere_bac"])
        groupe = canon(row["groupe_inphb"])
        poids = pd.to_numeric(row["poids"], errors="coerce")

        if serie not in coeffs_bac:
            raise ValueError(
                f"Série inconnue dans groupes_matieres : {serie}."
            )
        if matiere not in coeffs_bac[serie]:
            raise ValueError(
                f"Matière absente de coefficients_bac : {serie} / {matiere}."
            )
        if pd.isna(poids) or float(poids) <= 0:
            raise ValueError(
                f"Poids invalide dans groupes_matieres : {serie} / {matiere} / {groupe}."
            )

        lignes = groupes_temp.setdefault(serie, {}).setdefault(matiere, [])
        if any(g == groupe for g, _ in lignes):
            raise ValueError(
                f"Mapping dupliqué dans groupes_matieres : {serie} / {matiere} / {groupe}."
            )
        lignes.append((groupe, float(poids)))

    groupes_score_bac: Dict[str, Dict[str, Tuple[Tuple[str, float], ...]]] = {}
    for serie, matieres in coeffs_bac.items():
        groupes_score_bac[serie] = {}
        for matiere, coefficient_bac in matieres.items():
            mappings = groupes_temp.get(serie, {}).get(matiere)
            groupes_score_bac[serie][matiere] = tuple(
                mappings if mappings else [(matiere, float(coefficient_bac))]
            )

    inp = pd.read_excel(path, sheet_name="coefficients_inphb")
    inp["matiere"] = inp["matiere"].map(canon)
    return SimParams(
        fichier_excel=path,
        coeffs_bac=coeffs_bac,
        groupes_score_bac=groupes_score_bac,
        coeffs_inphb=inp,
        places=pd.read_excel(path, sheet_name="places_estimees"),
        mentions=pd.read_excel(path, sheet_name="mentions_inphb"),
        stats_series=pd.read_excel(path, sheet_name="stats_series_2025"),
        eligibilite=pd.read_excel(path, sheet_name="eligibilite_inphb"),
        liens_filieres=pd.read_excel(path, sheet_name="liens_filieres"),
    )

def canon(matiere: str) -> str:
    return ALIASES_MATIERES.get(str(matiere).strip(), str(matiere).strip())