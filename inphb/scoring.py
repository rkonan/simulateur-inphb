


from typing import Dict, Tuple

import pandas as pd

from inphb.parameters import SimParams




def profil_coefficients_filiere(
    params: SimParams,
    filiere: str,
    serie: str | None = None,
) -> str | None:
    """Retourne le profil de coefficients déclaré dans l'onglet d'éligibilité."""
    df = _eligibilite_normalisee(params)
    df = df[
        df["groupe_filiere"].astype(str).str.upper()
        == str(filiere).strip().upper()
    ]
    if serie is not None:
        serie_norm = str(serie).strip().upper()
        df = df[
            df["series_ou_BT_admissibles"].map(
                lambda c: serie_norm in {x.upper() for x in _series_cell(c)}
            )
        ]
    if df.empty:
        return None

    if "profil_coefficients" not in df.columns:
        # Compatibilité avec les anciens fichiers : une filière portant le
        # même code que sa formule peut encore être calculée.
        return str(filiere).strip()

    valeurs = df["profil_coefficients"].dropna().astype(str).str.strip()
    valeurs = valeurs[valeurs != ""]
    return valeurs.iloc[0] if not valeurs.empty else None



def denominateur_formule(params: SimParams, filiere: str, serie: str) -> float:
    """Somme automatiquement les coefficients de la formule applicable."""
    df = lignes_formule(params, filiere, serie)
    denominateur = float(df["coefficient_dossier"].sum())
    if denominateur <= 0:
        raise ValueError(
            f"Le dénominateur calculé est nul pour {filiere}, série {serie}."
        )
    return denominateur


def libelle_formule(params: SimParams, filiere: str, serie: str) -> str:
    """Génère une formule lisible depuis les matières et coefficients Excel."""
    df = lignes_formule(params, filiere, serie)
    termes = [
        f"{row.matiere}×{float(row.coefficient_dossier):g}"
        for row in df.itertuples(index=False)
    ]
    return " + ".join(termes) + f" / {denominateur_formule(params, filiere, serie):g}"


def formules_dossier(params: SimParams) -> pd.DataFrame:
    df = params.coeffs_inphb.copy()
    return df[~df["cycle"].astype(str).str.strip().str.lower().str.startswith("admission")].copy()


def lignes_formule(params: SimParams, filiere: str, serie: str) -> pd.DataFrame:
    """Retourne les matières et coefficients applicables à une filière/série.

    Le dénominateur et le libellé de la formule ne sont jamais lus dans Excel :
    ils sont déduits automatiquement des coefficients sélectionnés.
    """
    profil = profil_coefficients_filiere(params, filiere, serie)
    if not profil:
        raise ValueError(
            f"Aucun profil de coefficients n'est défini pour {filiere} "
            f"avec la série {serie}."
        )

    df = formules_dossier(params)
    df = df[
        df["concours_filiere"].astype(str).str.upper()
        == str(profil).strip().upper()
    ]
    df = df[
        df["series_autorisees"].map(
            lambda c: _serie_correspond_formule(serie, c)
        )
    ]
    if df.empty:
        raise ValueError(
            f"Les coefficients du profil {profil} manquent pour la série {serie} "
            f"(filière {filiere})."
        )

    df = df.copy()
    df["coefficient_dossier"] = pd.to_numeric(
        df["coefficient_dossier"], errors="coerce"
    )
    df = df.dropna(subset=["matiere", "coefficient_dossier"])
    df = df[df["coefficient_dossier"] > 0]
    if df.empty:
        raise ValueError(
            f"Aucun coefficient positif n'est défini pour le profil {profil}, "
            f"série {serie}."
        )
    return df


def calculer_mc_mgm(note_bac: float, moyennes: Dict[str, float]) -> Tuple[float, float]:
    mc = (2 * moyennes["2nde"] + 3 * moyennes["1ere"] + 5 * moyennes["tle"]) / 10
    mgm = (mc + 3 * note_bac) / 4
    return round(float(mc), 4), round(float(mgm), 4)



def calculer_candidat(
    params: SimParams,
    serie: str,
    mention: str,
    notes_bac: Dict[str, float],
    moyennes: Dict[str, Dict[str, float]],
    filieres: Iterable[str],
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    del mention  # La mention est informative lors d'une saisie réelle.
    rows, mgm_detail = [], {}

    for mat in sorted(notes_bac):
        mc, val = calculer_mc_mgm(notes_bac[mat], moyennes[mat])
        mgm_detail[mat] = val
        rows.append({
            "Matière": mat,
            "2nde": moyennes[mat]["2nde"],
            "1ère": moyennes[mat]["1ere"],
            "Terminale": moyennes[mat]["tle"],
            "Bac": notes_bac[mat],
            "MC": mc,
            "MGM": val,
        })

    mgm_score = construire_mgm_groupes(mgm_detail, serie, params)

    scores = {}
    for filiere in filieres:
        try:
            scores[filiere] = moyenne_dossier_inphb(
                mgm_score,
                params,
                filiere,
                serie,
            )
        except ValueError:
            pass

    return pd.DataFrame(rows), scores


def construire_mgm_groupes(
    mgm_detail: Dict[str, float],
    serie: str,
    params: SimParams,
) -> Dict[str, float]:
    """Construit les MGM utilisées dans les formules INP-HB.

    Les notes restent saisies et stockées au niveau des matières réelles du bac.
    Chaque groupe synthétique (par exemple ``MT``) est une moyenne pondérée par
    les poids définis dans l'onglet ``groupes_matieres``. Par défaut, ces poids
    reprennent les coefficients du bac.

    Une épreuve combinée peut alimenter plusieurs groupes, par exemple une ligne
    ``Maths & Sciences Physiques`` déclarée ``Maths|SP``.
    """
    if serie not in params.coeffs_bac:
        raise ValueError(f"Série inconnue dans coefficients_bac : {serie}")

    numerateurs: Dict[str, float] = {}
    denominateurs: Dict[str, float] = {}

    for matiere, valeur in mgm_detail.items():
        if matiere not in params.coeffs_bac[serie]:
            continue

        coefficient_bac = float(params.coeffs_bac[serie][matiere])
        mappings = params.groupes_score_bac.get(serie, {}).get(
            matiere,
            ((matiere, coefficient_bac),),
        )

        for groupe, poids in mappings:
            numerateurs[groupe] = (
                numerateurs.get(groupe, 0.0) + float(valeur) * float(poids)
            )
            denominateurs[groupe] = (
                denominateurs.get(groupe, 0.0) + float(poids)
            )

    groupes_mgm = dict(mgm_detail)
    for groupe, numerateur in numerateurs.items():
        denominateur = denominateurs[groupe]
        if denominateur > 0:
            groupes_mgm[groupe] = round(numerateur / denominateur, 4)

    return groupes_mgm

