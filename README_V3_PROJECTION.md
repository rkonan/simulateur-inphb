# V3 — Projection d'admissibilité par niveau

La page `pages/01_Projection.py` ajoute un module de simulation pour les élèves de Seconde, Première et Terminale.

Fonctionnalités :

- saisie des moyennes déjà connues selon le niveau ;
- projection des années futures et de la note du Bac ;
- curseur de progression générale ;
- curseurs par matière ;
- recalcul en temps réel des scores, rangs et probabilités ;
- comparaison avant/après pour toutes les filières compatibles ;
- choix d'une filière objectif ;
- analyse marginale de l'impact de chaque matière ;
- scénarios prudent, réaliste et ambitieux ;
- plan de progression par matière ;
- graphique de trajectoire ;
- rapport PDF téléchargeable.

## Dépendance ajoutée

`reportlab==4.4.3`

## Base statistique

La page utilise la même base `population_inphb_distributions.db` que l'analyse principale. En l'absence de la base, les scores restent calculables mais les probabilités et rangs sont indisponibles.
