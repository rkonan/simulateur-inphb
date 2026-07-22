
from parameters import SimParams
from typing import Dict,Tuple,List
import numpy as np
DISTRIBUTION_MENTIONS_EXTENSION = {
    "Très Bien": 0.02,
    "Bien": 0.28,
    "Assez Bien": 0.45,
    "Passable": 0.25,
}


BIAIS_SERIE = {
    "A1": {"Français": 0.8, "Anglais": 0.8, "Maths": -0.5},
    "A2": {"Français": 0.8, "Anglais": 0.9, "Maths": -0.7},
    "B": {"ScEco": 0.9, "Maths": 0.2, "Français": 0.2, "Anglais": 0.1},
    "C": {"Maths": 1.0, "SP": 0.8, "SVT": -0.2, "Français": -0.5, "Anglais": -0.2},
    "D": {"Maths": 0.1, "SP": 0.3, "SVT": 1.0, "Français": -0.2, "Anglais": -0.1},
    "E": {"Maths": 0.8, "SP": 0.7, "MT": 1.0, "Français": -0.5, "Anglais": -0.2},
}

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


def bornes_mention(mention: str, mentions_df: pd.DataFrame) -> Tuple[float, float]:
    row = mentions_df[mentions_df["mention"].astype(str) == str(mention)]
    if row.empty:
        raise ValueError(f"Mention inconnue : {mention}")
    return float(row.iloc[0]["borne_min_incluse"]), float(row.iloc[0]["borne_max_exclue"])


def distribution_mentions_extension(params: SimParams) -> Tuple[List[str], np.ndarray]:
    labels = params.mentions["mention"].astype(str).tolist()
    probs = np.array([DISTRIBUTION_MENTIONS_EXTENSION.get(label, 0.0) for label in labels], dtype=float)
    if probs.sum() <= 0:
        raise ValueError("La distribution des profils supplémentaires est vide.")
    return labels, probs / probs.sum()


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
    raw = {}
    for matiere in coeffs:
        mappings = params.groupes_score_bac.get(serie, {}).get(
            matiere, ((matiere, float(coeffs[matiere])),)
        )
        biais = biases.get(matiere)
        if biais is None:
            biais = max(
                (biases.get(groupe, 0.0) for groupe, _ in mappings),
                default=0.0,
            )
        raw[matiere] = float(
            np.clip(
                target + float(biais) + rng.normal(0, sigma_matiere),
                0,
                20,
            )
        )
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



def quotas_entiers(labels: List[str], proportions: np.ndarray, n: int) -> Dict[str, int]:
    p = np.asarray(proportions, dtype=float)
    p = p / p.sum()
    raw = p * n
    counts = np.floor(raw).astype(int)
    remainder = n - int(counts.sum())
    if remainder:
        counts[np.argsort(raw - counts)[-remainder:]] += 1
    return dict(zip(labels, counts.astype(int)))
