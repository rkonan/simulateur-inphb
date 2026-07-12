
# Simulateur INP-HB V4

Cette version sépare clairement deux analyses :

1. Analyse intrinsèque du dossier
   - calcul des MC et MGM ;
   - classement des scores dossier par filière ;
   - détail des coefficients et contributions par matière ;
   - première recommandation basée uniquement sur le dossier.

2. Analyse comparative d'admissibilité
   - comparaison à une population fictive ;
   - probabilité exacte et Monte-Carlo ;
   - rang moyen, médian, P10 et P90 ;
   - percentile ;
   - marge au seuil des 1 500 admissibles.

## Générer la population

```bash
python build_population_db.py \
  --params parametres_simulateur_inphb.xlsx \
  --db population_inphb.db \
  --n 200000 \
  --batch-size 5000 \
  --sigma-bac 2.4 \
  --part-profils-forts 0.60
```

## Lancer l'application

```bash
export ADMIN_PASSWORD="ton-mot-de-passe"
streamlit run app_streamlit.py
```
