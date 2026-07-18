from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

import numpy as np
import pandas as pd
#from scipy.stats import binom
from binom_local import binom

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

BIAIS_SERIE = {
    "A1": {"Français": 0.8, "Anglais": 0.8, "Maths": -0.5},
    "A2": {"Français": 0.8, "Anglais": 0.9, "Maths": -0.7},
    "B": {"ScEco": 0.9, "Maths": 0.2, "Français": 0.2, "Anglais": 0.1},
    "C": {"Maths": 1.0, "SP": 0.8, "SVT": -0.2, "Français": -0.5, "Anglais": -0.2},
    "D": {"Maths": 0.1, "SP": 0.3, "SVT": 1.0, "Français": -0.2, "Anglais": -0.1},
    "E": {"Maths": 0.8, "SP": 0.7, "MT": 1.0, "Français": -0.5, "Anglais": -0.2},
}

# Hypothèse V3 pour les 600 profils supplémentaires, plus faibles que les 900 admis.
# Cette distribution est volontairement plus large et doit rester paramétrable.
DISTRIBUTION_MENTIONS_EXTENSION = {
    "Très Bien": 0.02,
    "Bien": 0.28,
    "Assez Bien": 0.45,
    "Passable": 0.25,
}


def canon(matiere: str) -> str:
    return ALIASES_MATIERES.get(str(matiere).strip(), str(matiere).strip())


def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return "_".join(part for part in "".join(c if c.isalnum() else " " for c in text).lower().split())


@dataclass
class SimParams:
    fichier_excel: Path
    coeffs_bac: Dict[str, Dict[str, float]]
    coeffs_inphb: pd.DataFrame
    places: pd.DataFrame
    mentions: pd.DataFrame
    stats_series: pd.DataFrame
    eligibilite: pd.DataFrame


def charger_parametres(fichier_excel: str | Path) -> SimParams:
    path = Path(fichier_excel)
    bac = pd.read_excel(path, sheet_name="coefficients_bac").dropna(subset=["serie", "matiere", "coefficient"])
    coeffs_bac: Dict[str, Dict[str, float]] = {}
    for _, row in bac.iterrows():
        coeffs_bac.setdefault(str(row["serie"]).strip(), {})[canon(row["matiere"])] = float(row["coefficient"])

    inp = pd.read_excel(path, sheet_name="coefficients_inphb")
    inp["matiere"] = inp["matiere"].map(canon)
    return SimParams(
        fichier_excel=path,
        coeffs_bac=coeffs_bac,
        coeffs_inphb=inp,
        places=pd.read_excel(path, sheet_name="places_estimees"),
        mentions=pd.read_excel(path, sheet_name="mentions_inphb"),
        stats_series=pd.read_excel(path, sheet_name="stats_series_2025"),
        eligibilite=pd.read_excel(path, sheet_name="eligibilite_inphb"),
    )


def formules_dossier(params: SimParams) -> pd.DataFrame:
    df = params.coeffs_inphb.copy()
    return df[~df["cycle"].astype(str).str.strip().str.lower().str.startswith("admission")].copy()


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


def moyenne_ponderee(notes: Dict[str, float], coeffs: Dict[str, float]) -> float:
    vals = [(float(notes[m]), float(c)) for m, c in coeffs.items() if m in notes and pd.notna(notes[m])]
    return float(sum(v * c for v, c in vals) / sum(c for _, c in vals)) if vals else np.nan


def bornes_mention(mention: str, mentions_df: pd.DataFrame) -> Tuple[float, float]:
    row = mentions_df[mentions_df["mention"].astype(str) == str(mention)]
    if row.empty:
        raise ValueError(f"Mention inconnue : {mention}")
    return float(row.iloc[0]["borne_min_incluse"]), float(row.iloc[0]["borne_max_exclue"])


def mention_depuis_moyenne(moyenne: float, mentions_df: pd.DataFrame) -> str:
    for _, row in mentions_df.iterrows():
        if float(row["borne_min_incluse"]) <= moyenne < float(row["borne_max_exclue"]):
            return str(row["mention"])
    return "Non admis"


def quotas_entiers(labels: List[str], proportions: np.ndarray, n: int) -> Dict[str, int]:
    p = np.asarray(proportions, dtype=float)
    p = p / p.sum()
    raw = p * n
    counts = np.floor(raw).astype(int)
    remainder = n - int(counts.sum())
    if remainder:
        counts[np.argsort(raw - counts)[-remainder:]] += 1
    return dict(zip(labels, counts.astype(int)))


def distribution_series_globale(params: SimParams) -> Tuple[List[str], np.ndarray]:
    stats = params.stats_series.copy()
    stats["serie"] = stats["serie"].astype(str).str.strip()
    stats = stats[stats["serie"].isin(params.coeffs_bac)]
    if stats.empty:
        raise ValueError("Aucune série exploitable avec coefficients BAC.")
    weights = stats["admis_T"].astype(float).to_numpy()
    return stats["serie"].tolist(), weights / weights.sum()


def distribution_mentions_fortes(params: SimParams) -> Tuple[List[str], np.ndarray]:
    return (
        params.mentions["mention"].astype(str).tolist(),
        params.mentions["proportion"].astype(float).to_numpy(),
    )


def distribution_mentions_extension(params: SimParams) -> Tuple[List[str], np.ndarray]:
    labels = params.mentions["mention"].astype(str).tolist()
    probs = np.array([DISTRIBUTION_MENTIONS_EXTENSION.get(label, 0.0) for label in labels], dtype=float)
    if probs.sum() <= 0:
        raise ValueError("La distribution des profils supplémentaires est vide.")
    return labels, probs / probs.sum()


def construire_tableau(labels: List[str], probs: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    q = quotas_entiers(labels, probs, n)
    arr = np.array([label for label in labels for _ in range(q[label])], dtype=object)
    rng.shuffle(arr)
    return arr


def ajuster_notes_vers_moyenne(notes: Dict[str, float], coeffs: Dict[str, float], cible: float) -> Dict[str, float]:
    mats = list(coeffs)
    values = np.array([notes[m] for m in mats])
    weights = np.array([coeffs[m] for m in mats])
    lo, hi = -20.0, 20.0
    for _ in range(60):
        shift = (lo + hi) / 2
        mean = np.average(np.clip(values + shift, 0, 20), weights=weights)
        if mean < cible:
            lo = shift
        else:
            hi = shift
    adjusted = np.clip(values + (lo + hi) / 2, 0, 20)
    return {m: round(float(v), 2) for m, v in zip(mats, adjusted)}


def generer_notes_bac_pour_mention(
    serie: str,
    mention: str,
    params: SimParams,
    rng: np.random.Generator,
    sigma_matiere: float = 2.4,
) -> Dict[str, float]:
    coeffs = params.coeffs_bac[serie]
    low, high = bornes_mention(mention, params.mentions)
    target = float(rng.uniform(low + 0.05, min(high - 0.05, 19.95)))
    biases = BIAIS_SERIE.get(serie, {})
    raw = {
        m: float(np.clip(target + biases.get(m, 0) + rng.normal(0, sigma_matiere), 0, 20))
        for m in coeffs
    }
    return ajuster_notes_vers_moyenne(raw, coeffs, target)


def generer_moyennes_classe(
    note_bac: float,
    rng: np.random.Generator,
    sigma_niveau: float = 0.8,
    sigma_annee: float = 0.6,
    progression: float = 0.25,
) -> Dict[str, float]:
    level = note_bac + rng.normal(0, sigma_niveau)
    return {
        "2nde": round(float(np.clip(level - progression + rng.normal(0, sigma_annee), 0, 20)), 2),
        "1ere": round(float(np.clip(level + rng.normal(0, sigma_annee), 0, 20)), 2),
        "tle": round(float(np.clip(level + progression + rng.normal(0, sigma_annee), 0, 20)), 2),
    }


def calculer_mc_mgm(note_bac: float, moyennes: Dict[str, float]) -> Tuple[float, float]:
    mc = (2 * moyennes["2nde"] + 3 * moyennes["1ere"] + 5 * moyennes["tle"]) / 10
    mgm = (mc + 3 * note_bac) / 4
    return round(float(mc), 4), round(float(mgm), 4)


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


def calculer_candidat(
    params: SimParams,
    serie: str,
    mention: str,
    notes_bac: Dict[str, float],
    moyennes: Dict[str, Dict[str, float]],
    filieres: Iterable[str],
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    rows, mgm = [], {}
    for mat in sorted(notes_bac):
        mc, val = calculer_mc_mgm(notes_bac[mat], moyennes[mat])
        mgm[mat] = val
        rows.append({
            "Matière": mat,
            "2nde": moyennes[mat]["2nde"],
            "1ère": moyennes[mat]["1ere"],
            "Terminale": moyennes[mat]["tle"],
            "Bac": notes_bac[mat],
            "MC": mc,
            "MGM": val,
        })
    scores = {}
    for filiere in filieres:
        try:
            scores[filiere] = moyenne_dossier_inphb(mgm, params, filiere, serie)
        except ValueError:
            pass
    return pd.DataFrame(rows), scores


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

            for filiere in filieres_autorisees_serie(params, serie, filieres):
                try:
                    score = moyenne_dossier_inphb(mgm, params, filiere, serie)
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
