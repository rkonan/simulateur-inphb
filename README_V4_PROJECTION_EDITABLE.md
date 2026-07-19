# V4 — Projection éditable

## Évolutions

- Suppression du mode Automatique / Manuel.
- Les projections automatiques préremplissent désormais un tableau toujours éditable.
- L'élève peut modifier une, plusieurs ou toutes les matières.
- Le bouton **Recalculer les projections** applique les curseurs actuels et remplace le tableau.
- Le bouton **Restaurer les valeurs automatiques** annule les ajustements manuels et revient au dernier scénario automatique appliqué.
- Détection et affichage des matières modifiées manuellement.
- Les valeurs du tableau alimentent les scores, probabilités, rangs, analyses d'impact, scénarios, plan de progression, graphiques et rapport PDF.
- Les scénarios prudent, réaliste et ambitieux sont construits autour des valeurs réellement saisies dans le tableau.

## Vérifications réalisées

- Compilation Python de tout le projet avec `compileall`.
- Démarrage du serveur Streamlit en mode headless.
- Contrôle de l'endpoint de santé Streamlit.
- Rendu initial de la page Projection avec le framework de test Streamlit, sans exception.

## Lancement

```bash
pip install -r requirements.txt
streamlit run app.py
```
