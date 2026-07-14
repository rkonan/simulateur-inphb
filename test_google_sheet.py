from datetime import datetime
import requests
from datetime import UTC, datetime


# À remplacer
# WEB_APP_URL = (
#     "https://script.google.com/macros/s/"
#     "AKfycbyCTFYlfGUfS0Icc6bc3MqRfa4ZoRE7GR2maQFV08Ve3VVtfYItH1IkuM7jMIKzu1yCLw"
#     "/exec"
# )


WEB_APP_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbzTtOqqBtIAvNdMIdRu1_Dx0vdE0-XZQ_9f33MIlZ1ZzOIBLNhZIc3hzfQchcNpOjobtg"
    "/exec"
)


API_SECRET = "OyvlqXNc2CZrPVEbgMaAetCYtaC5uX9SPuUrcqkNtsAkbBF05oMsIVEbpQY0Hakb"

# payload = {
#     "secret": API_SECRET,
#     "profil": {
#         "analyse_id": "TEST-001",
#         "date_utc": datetime.utcnow().isoformat(),
#         "profil_hash": "test_hash",
#         "serie": "C",
#         "mention": "Bien",
#         "version_modele": "test",

#         "Maths_2nde": 15,
#         "Maths_1ere": 16,
#         "Maths_Terminale": 17,
#         "Maths_Bac": 18,

#         "Physique_2nde": 14,
#         "Physique_1ere": 15,
#         "Physique_Terminale": 16,
#         "Physique_Bac": 17,
#     },

#     "simulations": [
#         {
#             "simulation_id": "TEST-001-GI",
#             "analyse_id": "TEST-001",
#             "date_utc": datetime.utcnow().isoformat(),
#             "profil_hash": "test_hash",
#             "serie": "C",
#             "mention": "Bien",

#             "filiere": "Génie Informatique",
#             "score": 15.82,
#             "probabilite": 87.4,
#             "rang_moyen": 1234,
#             "rang_median": 1210,
#             "rang_p10": 950,
#             "rang_p90": 1480,
#             "percentile": 91.8,
#             "population_reference": 100000,
#             "dossiers_concurrents": 3000,
#             "seuil_admissibles": 2000,
#             "version_modele": "test",
#         }
#     ],
# }
payload = {
    "secret": API_SECRET,
    "commentaire": {
        "date_utc": datetime.now(UTC).isoformat(),
        "analyse_id": "TEST-COMMENTAIRE-001",
        "serie": "C",
        "mention": "Bien",
        "satisfaction": "Très utile",
        "commentaire": "Test d'envoi d'un commentaire.",
        "version_modele": "v5_distribution",
    },
}


response = requests.post(
    WEB_APP_URL,
    json=payload,
    timeout=30,
    allow_redirects=True,
)

print("HTTP :", response.status_code)
print("Réponse :", response.text)
print("Envoi...")

response = requests.post(
    WEB_APP_URL,
    json=payload,
    timeout=30,
)

print("HTTP :", response.status_code)
print(response.text)

try:
    print(response.json())
except Exception:
    pass