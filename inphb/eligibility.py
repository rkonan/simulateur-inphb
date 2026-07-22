
from pathlib import Path
from typing import Dict
def evaluer_admissibilite_depuis_db(
    path: str | Path,
    filiere: str,
    serie: str,
    score_candidat: float,
    nb_dossiers_concurrents: int,
    seuil_admissibles: int = 1500,
) -> Dict[str, float | int | str | bool | None]:
    """Évalue l'admissibilité pour un couple précis filière/série.

    La table ``distributions_scores`` doit contenir les colonnes ``filiere``,
    ``serie``, ``score_arrondi`` et ``effectif``. Une ancienne base agrégée
    uniquement par filière est volontairement déclarée indisponible afin de ne
    pas mélanger des séries dont les profils statistiques sont différents.
    """
    score_reference = round(float(score_candidat), 2)
    colonnes = _colonnes_distributions(path)
    requises = {"filiere", "serie", "score_arrondi", "effectif"}
    if not requises.issubset(colonnes):
        return _resultat_indisponible(
            score_reference,
            nb_dossiers_concurrents,
            seuil_admissibles,
            "La base statistique ne contient pas de distribution par série.",
        )

    try:
        with sqlite3.connect(path) as con:
            population, nb_devant, nb_inferieurs_ou_egaux = con.execute(
                """
                SELECT
                    COALESCE(SUM(effectif), 0) AS population,
                    COALESCE(SUM(CASE WHEN score_arrondi > ? THEN effectif ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN score_arrondi <= ? THEN effectif ELSE 0 END), 0)
                FROM distributions_scores
                WHERE UPPER(TRIM(filiere)) = UPPER(TRIM(?))
                  AND UPPER(TRIM(serie)) = UPPER(TRIM(?))
                """,
                (score_reference, score_reference, str(filiere), str(serie)),
            ).fetchone()
    except sqlite3.Error as exc:
        return _resultat_indisponible(
            score_reference,
            nb_dossiers_concurrents,
            seuil_admissibles,
            f"Erreur de lecture de la base statistique : {exc}",
        )

    population = int(population or 0)
    nb_devant = int(nb_devant or 0)
    nb_inferieurs_ou_egaux = int(nb_inferieurs_ou_egaux or 0)

    if population == 0:
        return _resultat_indisponible(
            score_reference,
            nb_dossiers_concurrents,
            seuil_admissibles,
            f"Aucune distribution disponible pour {filiere} avec la série {serie}.",
        )

    if (
        nb_dossiers_concurrents <= 0
        or seuil_admissibles <= 0
        or nb_dossiers_concurrents < seuil_admissibles
    ):
        return _resultat_indisponible(
            score_reference,
            nb_dossiers_concurrents,
            seuil_admissibles,
            "Paramètres de projection invalides.",
        )

    proportion_devant = nb_devant / population
    percentile = nb_inferieurs_ou_egaux / population * 100
    distribution_rang = binom(
        n=int(nb_dossiers_concurrents),
        p=float(proportion_devant),
    )
    probabilite_exacte = float(
        distribution_rang.cdf(int(seuil_admissibles) - 1) * 100
    )

    return {
        "disponible": True,
        "raison_indisponibilite": None,
        "probabilite": probabilite_exacte,
        "probabilite_exacte": probabilite_exacte,
        "rang_moyen": float(1 + nb_dossiers_concurrents * proportion_devant),
        "rang_median": float(1 + distribution_rang.ppf(0.50)),
        "rang_p10": float(1 + distribution_rang.ppf(0.10)),
        "rang_p90": float(1 + distribution_rang.ppf(0.90)),
        "percentile": float(percentile),
        "population": population,
        "seuil_admissibles": int(seuil_admissibles),
        "dossiers_concurrents": int(nb_dossiers_concurrents),
        "proportion_devant": float(proportion_devant),
        "score_reference": score_reference,
    }


# Alias temporaire pour éviter de casser d'anciens imports.
def evaluer_candidat_depuis_db(
    path: str | Path,
    filiere: str,
    score_candidat: float,
    nb_concurrents: int,
    nb_places: int,
    nb_tirages: int = 5000,
    seed: int = 123,
    serie: str = "",
) -> Dict[str, float | int | str | bool | None]:
    """Alias de compatibilité.

    ``nb_tirages`` et ``seed`` sont conservés dans la signature pour les
    anciens appels, mais ne sont plus utilisés.
    """
    return evaluer_admissibilite_depuis_db(
        path=path,
        filiere=filiere,
        serie=serie,
        score_candidat=score_candidat,
        nb_dossiers_concurrents=nb_concurrents,
        seuil_admissibles=nb_places,
    )
