from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from streamlit_js_eval import streamlit_js_eval


@dataclass(frozen=True)
class LocalisationNavigateur:
    """Informations approximatives fournies par le navigateur.

    Aucune adresse IP, coordonnée GPS ou adresse précise n'est collectée.
    """

    fuseau_horaire: str | None = None
    langue: str | None = None
    langues: list[str] | None = None
    largeur_ecran: int | None = None
    hauteur_ecran: int | None = None
    largeur_fenetre: int | None = None
    type_appareil: str | None = None

    # Champs prévus pour un enrichissement futur.
    pays: str | None = None
    code_pays: str | None = None
    region: str | None = None
    ville: str | None = None
    source_localisation: str | None = None

    def en_dict(self) -> dict[str, Any]:
        return {
            cle: valeur
            for cle, valeur in asdict(self).items()
            if valeur is not None
        }

    def en_json(self) -> str:
        return json.dumps(
            self.en_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def recuperer_localisation_navigateur(
    *,
    key: str = "localisation_navigateur",
) -> LocalisationNavigateur | None:
    """Utilise ipwho.is depuis le navigateur, avec repli navigateur.

    Le résultat IP est gardé dans sessionStorage. Au premier passage, seules
    les données du navigateur peuvent être disponibles. Au rerun suivant,
    les données IP sont utilisées si l'appel a réussi.
    """

    expression = """
(() => {
    const fallback = {
        fuseau_horaire:
            Intl.DateTimeFormat().resolvedOptions().timeZone || null,
        langue: navigator.language || null,
        langues: Array.isArray(navigator.languages)
            ? navigator.languages
            : null,
        largeur_ecran: Number.isFinite(screen.width)
            ? screen.width
            : null,
        hauteur_ecran: Number.isFinite(screen.height)
            ? screen.height
            : null,
        largeur_fenetre: Number.isFinite(window.innerWidth)
            ? window.innerWidth
            : null,
        pays: null,
        code_pays: null,
        region: null,
        ville: null,
        source_localisation: "navigateur"
    };

    const cacheKey = "inphb_ipwho_localisation_v1";
    const cached = sessionStorage.getItem(cacheKey);

    if (cached) {
        try {
            return JSON.stringify({
                ...fallback,
                ...JSON.parse(cached),
                source_localisation: "ipwho.is"
            });
        } catch (_) {
            sessionStorage.removeItem(cacheKey);
        }
    }

    const pendingKey = cacheKey + "_pending";

    if (!sessionStorage.getItem(pendingKey)) {
        sessionStorage.setItem(pendingKey, "1");

        fetch("https://ipwho.is/", {
            method: "GET",
            cache: "no-store"
        })
        .then(response => {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            return response.json();
        })
        .then(data => {
            if (!data || data.success === false) {
                throw new Error("Réponse ipwho.is invalide");
            }

            sessionStorage.setItem(
                cacheKey,
                JSON.stringify({
                    pays: data.country || null,
                    code_pays: data.country_code || null,
                    region: data.region || null,
                    ville: data.city || null
                })
            );
        })
        .catch(() => {
            sessionStorage.removeItem(cacheKey);
        })
        .finally(() => {
            sessionStorage.removeItem(pendingKey);
        });
    }

    return JSON.stringify(fallback);
})()
"""

    valeur = streamlit_js_eval(
        js_expressions=expression,
        key=key,
    )

    if not valeur:
        return None

    try:
        donnees = json.loads(valeur)
    except (TypeError, json.JSONDecodeError):
        return None

    largeur = donnees.get("largeur_fenetre")
    type_appareil = None

    if isinstance(largeur, (int, float)):
        if largeur < 768:
            type_appareil = "mobile"
        elif largeur < 1024:
            type_appareil = "tablette"
        else:
            type_appareil = "ordinateur"

    langues = donnees.get("langues")
    if not isinstance(langues, list):
        langues = None

    return LocalisationNavigateur(
        fuseau_horaire=donnees.get("fuseau_horaire"),
        langue=donnees.get("langue"),
        langues=langues,
        largeur_ecran=_entier_ou_none(
            donnees.get("largeur_ecran")
        ),
        hauteur_ecran=_entier_ou_none(
            donnees.get("hauteur_ecran")
        ),
        largeur_fenetre=_entier_ou_none(
            donnees.get("largeur_fenetre")
        ),
        type_appareil=type_appareil,
        pays=donnees.get("pays"),
        code_pays=donnees.get("code_pays"),
        region=donnees.get("region"),
        ville=donnees.get("ville"),
        source_localisation=donnees.get(
            "source_localisation"
        ),
    )

def _entier_ou_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
