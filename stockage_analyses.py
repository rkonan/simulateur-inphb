from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol, Sequence

import requests


class ErreurSauvegarde(RuntimeError):
    """Erreur technique lors de la sauvegarde d'une analyse."""


@dataclass(frozen=True)
class AnalyseCandidat:
    serie: str
    mention: str
    notes_bac: Mapping[str, float]
    moyennes: Mapping[str, Mapping[str, float]]
    resultats: Sequence[Mapping[str, Any]]
    version_modele: str = "v5_distribution"


class StockageAnalyses(Protocol):
    def sauvegarder(self, analyse: AnalyseCandidat) -> str:
        """Sauvegarde une analyse et retourne son identifiant."""


class StockageDesactive:
    def sauvegarder(self, analyse: AnalyseCandidat) -> str:
        return ""


class StockageGoogleSheets:
    def __init__(
        self,
        web_app_url: str,
        api_secret: str,
        timeout_secondes: float = 15.0,
    ) -> None:
        self.web_app_url = str(web_app_url).strip()
        self.api_secret = str(api_secret).strip()
        self.timeout_secondes = float(timeout_secondes)

        if not self.web_app_url:
            raise ValueError("L'URL du Web App Google Sheets est vide.")
        if not self.api_secret:
            raise ValueError("Le secret Google Sheets est vide.")

    def sauvegarder(self, analyse: AnalyseCandidat) -> str:
        payload = construire_payload_google_sheets(analyse)

        try:
            response = requests.post(
                self.web_app_url,
                json={"secret": self.api_secret, **payload},
                timeout=self.timeout_secondes,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise ErreurSauvegarde(
                f"Impossible de joindre Google Sheets : {exc}"
            ) from exc

        if not response.ok:
            raise ErreurSauvegarde(
                f"Google Apps Script a répondu HTTP {response.status_code} : "
                f"{response.text[:500]}"
            )

        try:
            contenu = response.json()
        except ValueError as exc:
            raise ErreurSauvegarde(
                "La réponse Google Apps Script n'est pas un JSON valide."
            ) from exc

        if not contenu.get("ok"):
            raise ErreurSauvegarde(
                str(contenu.get("error", "Échec de sauvegarde inconnu."))
            )

        return str(
            contenu.get("analyse_id")
            or payload["profil"]["analyse_id"]
        )


def _scalaire_json(value: Any) -> Any:
    if value is None:
        return None

    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass

    if isinstance(value, float) and value != value:
        return None

    return value


def _profil_canonique(analyse: AnalyseCandidat) -> dict[str, Any]:
    return {
        "serie": str(analyse.serie),
        "mention": str(analyse.mention),
        "notes_bac": {
            str(matiere): float(note)
            for matiere, note in sorted(analyse.notes_bac.items())
        },
        "moyennes": {
            str(matiere): {
                str(annee): float(note)
                for annee, note in sorted(valeurs.items())
            }
            for matiere, valeurs in sorted(analyse.moyennes.items())
        },
    }


def _aplatir_notes(analyse: AnalyseCandidat) -> dict[str, float | None]:
    colonnes: dict[str, float | None] = {}
    matieres = sorted(set(analyse.notes_bac) | set(analyse.moyennes))

    for matiere in matieres:
        valeurs = analyse.moyennes.get(matiere, {})
        colonnes[f"{matiere}_2nde"] = (
            float(valeurs["2nde"])
            if valeurs.get("2nde") is not None else None
        )
        colonnes[f"{matiere}_1ere"] = (
            float(valeurs["1ere"])
            if valeurs.get("1ere") is not None else None
        )
        colonnes[f"{matiere}_Terminale"] = (
            float(valeurs["tle"])
            if valeurs.get("tle") is not None else None
        )
        colonnes[f"{matiere}_Bac"] = (
            float(analyse.notes_bac[matiere])
            if analyse.notes_bac.get(matiere) is not None else None
        )

    return colonnes


def construire_payload_google_sheets(
    analyse: AnalyseCandidat,
) -> dict[str, Any]:
    if not analyse.resultats:
        raise ValueError("Aucun résultat à sauvegarder.")

    analyse_id = str(uuid.uuid4())
    date_utc = datetime.now(UTC).isoformat()

    profil_canonique = _profil_canonique(analyse)
    profil_json = json.dumps(
        profil_canonique,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    profil_hash = hashlib.sha256(
        profil_json.encode("utf-8")
    ).hexdigest()

    profil = {
        "analyse_id": analyse_id,
        "date_utc": date_utc,
        "profil_hash": profil_hash,
        "serie": str(analyse.serie),
        "mention": str(analyse.mention),
        "version_modele": str(analyse.version_modele),
        **_aplatir_notes(analyse),
        "profil_json": profil_json,
    }

    simulations: list[dict[str, Any]] = []

    for resultat in analyse.resultats:
        filiere = str(resultat.get("filiere", "")).strip()
        if not filiere:
            continue

        simulations.append(
            {
                "simulation_id": f"{analyse_id}:{filiere}",
                "analyse_id": analyse_id,
                "date_utc": date_utc,
                "profil_hash": profil_hash,
                "serie": str(analyse.serie),
                "mention": str(analyse.mention),
                "filiere": filiere,
                "score": _scalaire_json(resultat.get("score")),
                "probabilite": _scalaire_json(resultat.get("probabilite")),
                "rang_moyen": _scalaire_json(resultat.get("rang_moyen")),
                "rang_median": _scalaire_json(resultat.get("rang_median")),
                "rang_p10": _scalaire_json(resultat.get("rang_p10")),
                "rang_p90": _scalaire_json(resultat.get("rang_p90")),
                "percentile": _scalaire_json(resultat.get("percentile")),
                "population_reference": _scalaire_json(
                    resultat.get("population")
                ),
                "dossiers_concurrents": _scalaire_json(
                    resultat.get("dossiers_concurrents")
                ),
                "seuil_admissibles": _scalaire_json(
                    resultat.get("seuil_admissibles")
                ),
                "version_modele": str(analyse.version_modele),
            }
        )

    if not simulations:
        raise ValueError("Aucune filière valide à sauvegarder.")

    return {"profil": profil, "simulations": simulations}
