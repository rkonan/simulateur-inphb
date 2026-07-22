from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple
import streamlit as st
import numpy as np
import pandas as pd
#from scipy.stats import binom
from binom_local import binom
from inphb.parameters import SimParams



# Hypothèse V3 pour les 600 profils supplémentaires, plus faibles que les 900 admis.
# Cette distribution est volontairement plus large et doit rester paramétrable.
DISTRIBUTION_MENTIONS_EXTENSION = {
    "Très Bien": 0.02,
    "Bien": 0.28,
    "Assez Bien": 0.45,
    "Passable": 0.25,
}





def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return "_".join(part for part in "".join(c if c.isalnum() else " " for c in text).lower().split())


def _series_cell(cell: object) -> List[str]:
    return [x.strip() for x in str(cell).split(",") if x.strip()]


def _eligibilite_normalisee(params: SimParams) -> pd.DataFrame:
    """Retourne les lignes d'éligibilité exploitables du fichier Excel.

    L'onglet ``eligibilite_inphb`` est l'unique source de vérité pour savoir
    quelles séries peuvent sélectionner quelles filières. Les coefficients ne
    servent qu'au calcul du score et la base SQLite uniquement à la comparaison
    statistique.
    """
    df = params.eligibilite.copy()
    colonnes_requises = {"groupe_filiere", "series_ou_BT_admissibles"}
    manquantes = colonnes_requises.difference(df.columns)
    if manquantes:
        raise ValueError(
            "Colonnes manquantes dans eligibilite_inphb : "
            + ", ".join(sorted(manquantes))
        )

    df = df.dropna(subset=["groupe_filiere", "series_ou_BT_admissibles"]).copy()
    df["groupe_filiere"] = df["groupe_filiere"].astype(str).str.strip()
    return df[df["groupe_filiere"] != ""]


def filieres_autorisees_serie(
    params: SimParams,
    serie: str,
    disponibles: Iterable[str] | None = None,
) -> List[str]:
    """Liste les filières autorisées par l'onglet ``eligibilite_inphb``.

    ``disponibles`` est conservé uniquement pour compatibilité avec d'anciens
    appels. Il ne doit plus être utilisé pour filtrer l'offre à partir de la
    base statistique.
    """
    del disponibles
    serie_normalisee = str(serie).strip().upper()
    df = _eligibilite_normalisee(params)

    resultat = {
        str(row["groupe_filiere"]).strip()
        for _, row in df.iterrows()
        if serie_normalisee
        in {x.upper() for x in _series_cell(row["series_ou_BT_admissibles"])}
    }
    return sorted(resultat)


def series_autorisees_filiere(params: SimParams, filiere: str) -> List[str]:
    df = _eligibilite_normalisee(params)
    df = df[
        df["groupe_filiere"].astype(str).str.upper()
        == str(filiere).strip().upper()
    ]
    out = set()
    for cell in df["series_ou_BT_admissibles"].dropna():
        out.update(_series_cell(cell))
    return sorted(x for x in out if x in params.coeffs_bac)


def _serie_correspond_formule(serie: str, cell: object) -> bool:
    """Teste une série précise contre les groupes utilisés dans les formules.

    Dans ``coefficients_inphb``, les libellés génériques ``F`` et ``BT``
    désignent respectivement toutes les séries F et tous les brevets de
    technicien. L'autorisation exacte reste portée par ``eligibilite_inphb``.
    """
    serie_norm = str(serie).strip().upper()
    for token in (x.upper() for x in _series_cell(cell)):
        if token == serie_norm:
            return True
        if token == "F" and serie_norm.startswith("F"):
            return True
        if token == "BT" and serie_norm.startswith("BT"):
            return True
    return False




def moyenne_ponderee(notes: Dict[str, float], coeffs: Dict[str, float]) -> float:
    vals = [(float(notes[m]), float(c)) for m, c in coeffs.items() if m in notes and pd.notna(notes[m])]
    return float(sum(v * c for v, c in vals) / sum(c for _, c in vals)) if vals else np.nan




def mention_depuis_moyenne(moyenne: float, mentions_df: pd.DataFrame) -> str:
    for _, row in mentions_df.iterrows():
        if float(row["borne_min_incluse"]) <= moyenne < float(row["borne_max_exclue"]):
            return str(row["mention"])
    return "Non admis"






def construire_tableau(labels: List[str], probs: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    q = quotas_entiers(labels, probs, n)
    arr = np.array([label for label in labels for _ in range(q[label])], dtype=object)
    rng.shuffle(arr)
    return arr




def matieres_reelles_pour_formules(
    params: SimParams,
    serie: str,
    filieres: Iterable[str],
) -> List[str]:
    """Retourne les matières réelles à saisir pour les filières demandées."""
    groupes_requis: set[str] = set()
    for filiere in filieres:
        try:
            groupes_requis.update(
                lignes_formule(params, filiere, serie)["matiere"]
                .map(canon)
                .astype(str)
                .tolist()
            )
        except ValueError:
            continue

    matieres = []
    for matiere in params.coeffs_bac.get(serie, {}):
        mappings = params.groupes_score_bac.get(serie, {}).get(
            matiere,
            ((matiere, float(params.coeffs_bac[serie][matiere])),),
        )
        if any(groupe in groupes_requis for groupe, _ in mappings):
            matieres.append(matiere)

    return sorted(matieres)


def detail_groupes_score(
    mgm_detail: Dict[str, float],
    serie: str,
    params: SimParams,
) -> pd.DataFrame:
    """Produit le détail de construction des groupes synthétiques."""
    lignes = []
    for matiere, valeur in mgm_detail.items():
        coefficient_bac = float(
            params.coeffs_bac.get(serie, {}).get(matiere, 0.0)
        )
        mappings = params.groupes_score_bac.get(serie, {}).get(
            matiere, ((matiere, coefficient_bac),)
        )
        for groupe, poids in mappings:
            lignes.append(
                {
                    "Groupe score": groupe,
                    "Matière BAC": matiere,
                    "MGM matière": round(float(valeur), 4),
                    "Coefficient BAC": coefficient_bac,
                    "Poids groupe": float(poids),
                    "Contribution": round(float(valeur) * float(poids), 4),
                }
            )

    return pd.DataFrame(lignes)


def moyenne_dossier_inphb(mgm: Dict[str, float], params: SimParams, filiere: str, serie: str) -> float:
    df = lignes_formule(params, filiere, serie)
    denominator = denominateur_formule(params, filiere, serie)
    total = 0.0
    for _, row in df.iterrows():
        mat = canon(row["matiere"])
        if mat not in mgm:
            raise ValueError(f"Matière manquante : {mat}")
        total += mgm[mat] * float(row["coefficient_dossier"])
    return round(total / denominator, 4)


def initialiser_db_population(path: str | Path) -> None:
    path = Path(path)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as con:
        con.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE candidats(
            candidate_id INTEGER PRIMARY KEY,
            serie TEXT NOT NULL,
            mention TEXT NOT NULL,
            moyenne_bac_simulee REAL NOT NULL,
            groupe_reference TEXT NOT NULL,
            version_modele TEXT NOT NULL
        );
        CREATE TABLE notes_candidats(
            candidate_id INTEGER NOT NULL,
            matiere TEXT NOT NULL,
            note_bac REAL NOT NULL,
            moyenne_2nde REAL NOT NULL,
            moyenne_1ere REAL NOT NULL,
            moyenne_terminale REAL NOT NULL,
            mc REAL NOT NULL,
            mgm REAL NOT NULL,
            PRIMARY KEY(candidate_id,matierE),
            FOREIGN KEY(candidate_id) REFERENCES candidats(candidate_id)
        );
        CREATE TABLE scores(
            candidate_id INTEGER NOT NULL,
            filiere TEXT NOT NULL,
            score REAL NOT NULL,
            PRIMARY KEY(candidate_id,filiere),
            FOREIGN KEY(candidate_id) REFERENCES candidats(candidate_id)
        );
        CREATE TABLE model_metadata(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
        CREATE INDEX idx_candidats_serie_mention ON candidats(serie,mention);
        CREATE INDEX idx_candidats_groupe ON candidats(groupe_reference);
        CREATE INDEX idx_notes_matiere ON notes_candidats(matiere);
        CREATE INDEX idx_scores_filiere_score ON scores(filiere,score);
        CREATE VIEW population_scores AS
            SELECT s.filiere,
                   CAST(c.candidate_id AS TEXT) candidate_id,
                   c.serie,
                   c.mention,
                   c.groupe_reference,
                   c.moyenne_bac_simulee moyenne_bac,
                   s.score
            FROM scores s
            JOIN candidats c ON c.candidate_id=s.candidate_id;
        """)


def inserer_lot_population(path: str | Path, candidats: pd.DataFrame, notes: pd.DataFrame, scores: pd.DataFrame) -> None:
    with sqlite3.connect(path) as con:
        candidats.to_sql("candidats", con, if_exists="append", index=False)
        notes.to_sql("notes_candidats", con, if_exists="append", index=False)
        if not scores.empty:
            scores.to_sql("scores", con, if_exists="append", index=False)


def generer_population_unique_par_lots(
    params: SimParams,
    n: int,
    seed: int = 42,
    taille_lot: int = 5000,
    sigma_bac: float = 2.4,
    version_modele: str = "v3",
    proportion_profils_forts: float = 0.60,
) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    """Génère une seule population de candidats.

    V3 mélange deux groupes :
    - profils_forts : distribution des mentions observée parmi les 900 admis ;
    - extension : distribution plus large pour représenter les 600 profils
      supplémentaires et éviter une population artificiellement trop forte.
    """
    if not 0 < proportion_profils_forts < 1:
        raise ValueError("proportion_profils_forts doit être comprise entre 0 et 1.")

    rng = np.random.default_rng(seed)
    series, ps = distribution_series_globale(params)
    series_arr = construire_tableau(series, ps, n, rng)

    n_forts = int(round(n * proportion_profils_forts))
    n_extension = n - n_forts
    labels_forts, probs_forts = distribution_mentions_fortes(params)
    labels_ext, probs_ext = distribution_mentions_extension(params)

    mentions_fortes = construire_tableau(labels_forts, probs_forts, n_forts, rng)
    mentions_extension = construire_tableau(labels_ext, probs_ext, n_extension, rng)
    mention_arr = np.concatenate([mentions_fortes, mentions_extension])
    groupe_arr = np.array(["profils_forts"] * n_forts + ["extension"] * n_extension, dtype=object)

    permutation = rng.permutation(n)
    mention_arr = mention_arr[permutation]
    groupe_arr = groupe_arr[permutation]

    filieres = sorted(formules_dossier(params)["concours_filiere"].dropna().astype(str).unique())

    for start in range(0, n, taille_lot):
        cand_rows, note_rows, score_rows = [], [], []
        for pos in range(start, min(start + taille_lot, n)):
            cid = pos + 1
            serie = str(series_arr[pos])
            mention = str(mention_arr[pos])
            groupe = str(groupe_arr[pos])

            notes_bac = generer_notes_bac_pour_mention(serie, mention, params, rng, sigma_bac)
            mgm: Dict[str, float] = {}
            for mat, bac in notes_bac.items():
                class_avg = generer_moyennes_classe(bac, rng)
                mc, m = calculer_mc_mgm(bac, class_avg)
                mgm[mat] = m
                note_rows.append({
                    "candidate_id": cid,
                    "matiere": mat,
                    "note_bac": bac,
                    "moyenne_2nde": class_avg["2nde"],
                    "moyenne_1ere": class_avg["1ere"],
                    "moyenne_terminale": class_avg["tle"],
                    "mc": mc,
                    "mgm": m,
                })

            cand_rows.append({
                "candidate_id": cid,
                "serie": serie,
                "mention": mention,
                "moyenne_bac_simulee": round(moyenne_ponderee(notes_bac, params.coeffs_bac[serie]), 4),
                "groupe_reference": groupe,
                "version_modele": version_modele,
            })

            mgm_score = construire_mgm_groupes(mgm, serie, params)

            for filiere in filieres_autorisees_serie(params, serie, filieres):
                try:
                    score = moyenne_dossier_inphb(
                        mgm_score,
                        params,
                        filiere,
                        serie,
                    )
                except ValueError:
                    continue
                score_rows.append({"candidate_id": cid, "filiere": filiere, "score": score})

        yield pd.DataFrame(cand_rows), pd.DataFrame(note_rows), pd.DataFrame(score_rows)


def _colonnes_distributions(path: str | Path) -> set[str]:
    """Retourne les colonnes de la table agrégée, ou un ensemble vide."""
    path = Path(path)
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(path) as con:
            rows = con.execute("PRAGMA table_info(distributions_scores)").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[1]) for row in rows}


def lister_filieres_db(path: str | Path, serie: str | None = None) -> List[str]:
    """Liste les filières disponibles dans la base statistique.

    Cette fonction ne pilote plus l'éligibilité de l'interface. Lorsque
    ``serie`` est fournie, seules les distributions de cette série sont
    retournées. Une ancienne base dépourvue de la colonne ``serie`` n'est pas
    considérée comme exploitable pour une analyse par couple filière/série.
    """
    colonnes = _colonnes_distributions(path)
    if not {"filiere", "score_arrondi", "effectif"}.issubset(colonnes):
        return []
    if serie is not None and "serie" not in colonnes:
        return []

    try:
        with sqlite3.connect(path) as con:
            if serie is None:
                rows = con.execute(
                    "SELECT DISTINCT filiere FROM distributions_scores ORDER BY filiere"
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT DISTINCT filiere
                    FROM distributions_scores
                    WHERE UPPER(TRIM(serie)) = UPPER(TRIM(?))
                    ORDER BY filiere
                    """,
                    (str(serie),),
                ).fetchall()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def statistique_disponible(
    path: str | Path,
    filiere: str,
    serie: str,
) -> bool:
    """Indique si une distribution existe pour le couple filière/série."""
    colonnes = _colonnes_distributions(path)
    requises = {"filiere", "serie", "score_arrondi", "effectif"}
    if not requises.issubset(colonnes):
        return False

    try:
        with sqlite3.connect(path) as con:
            row = con.execute(
                """
                SELECT COALESCE(SUM(effectif), 0)
                FROM distributions_scores
                WHERE UPPER(TRIM(filiere)) = UPPER(TRIM(?))
                  AND UPPER(TRIM(serie)) = UPPER(TRIM(?))
                """,
                (str(filiere), str(serie)),
            ).fetchone()
    except sqlite3.Error:
        return False
    return int((row or [0])[0] or 0) > 0


def _resultat_indisponible(
    score_reference: float,
    nb_dossiers_concurrents: int,
    seuil_admissibles: int,
    raison: str,
) -> Dict[str, float | int | str | bool | None]:
    return {
        "disponible": False,
        "raison_indisponibilite": raison,
        "probabilite": None,
        "probabilite_exacte": None,
        "rang_moyen": None,
        "rang_median": None,
        "rang_p10": None,
        "rang_p90": None,
        "percentile": None,
        "population": 0,
        "seuil_admissibles": int(seuil_admissibles),
        "dossiers_concurrents": int(nb_dossiers_concurrents),
        "proportion_devant": None,
        "score_reference": score_reference,
    }

