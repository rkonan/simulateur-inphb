# Simulateur INP-HB — version filières cliquables

Cette version conserve la refonte des matières techniques : les notes sont saisies sur les matières réelles du bac et les groupes INP-HB tels que `MT` sont calculés depuis l'onglet `groupes_matieres`.

## Nouveautés de présentation

- les noms des filières sont cliquables dans les classements ;
- les cartes du Top 3 affichent l'école et l'intitulé de la formation ;
- un bouton **En savoir plus** ouvre une source officielle INP-HB ;
- l'onglet Excel `liens_filieres` centralise les écoles, cycles, intitulés et URL ;
- le tableau comparatif est responsive et mieux adapté aux téléphones.

Les liens de PME, CGP et TSAERO pointent provisoirement vers le portail général de l'INP-HB, faute de fiche publique spécifique et stable identifiée. Ils peuvent être remplacés directement dans l'onglet `liens_filieres`, sans modification du code.

## Lancer l'application

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Reconstruire la population et les distributions

```bash
python build_population_et_distributions_db.py \
  --params parametres_simulateur_inphb.xlsx \
  --population-db population_inphb.db \
  --distributions-db population_inphb_distributions.db \
  --n 200000 \
  --batch-size 5000
```

Consulte aussi `README_REFONTE_MT.md` pour la logique de construction des groupes de matières.

## Secrets

Le fichier `.streamlit/secrets.toml` n'est pas inclus dans l'archive. Utilise `.streamlit/secrets.toml.example` comme modèle afin d'éviter de publier des identifiants sur GitHub.
