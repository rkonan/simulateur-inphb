# Refonte des matières techniques — onglet `groupes_matieres`

## Architecture

Les responsabilités sont désormais séparées :

- `coefficients_bac` : matières réelles du bac et coefficients officiels/estimés ;
- `groupes_matieres` : correspondance entre les matières du bac et les groupes utilisés dans les formules INP-HB (`MT`, `Maths`, `SP`, etc.) ;
- `coefficients_inphb` : coefficients de calcul du score dossier par filière ;
- `eligibilite_inphb` : filières accessibles et profil de coefficients applicable.

La colonne `groupe_score` n'est donc plus utilisée dans `coefficients_bac`.

## Structure de `groupes_matieres`

| Colonne | Rôle |
|---|---|
| `serie` | Série du bac ou BT |
| `matiere_bac` | Matière réelle figurant dans `coefficients_bac` |
| `groupe_inphb` | Matière synthétique attendue dans les formules INP-HB |
| `poids` | Poids de la matière dans le calcul du groupe |
| `source` | Source du paramétrage |
| `commentaire` | Précision éventuelle |

Une matière combinée peut alimenter plusieurs groupes : il suffit de créer plusieurs lignes dans `groupes_matieres`.

## Exemple F1

- Épreuve de spécialité / TP, poids 9 → `MT`
- Construction / Technologie, poids 6 → `MT`

Ainsi :

`MT = (MGM_spécialité × 9 + MGM_construction × 6) / 15`

Pour des MGM de 15 et 13 :

`MT = (15 × 9 + 13 × 6) / 15 = 14,20`

## Comportement du moteur

1. L'application saisit les matières réelles du bac.
2. Le moteur calcule MC et MGM pour chaque matière réelle.
3. Il construit les groupes INP-HB avec les poids de `groupes_matieres`.
4. Il applique ensuite les coefficients de `coefficients_inphb`.
5. La population synthétique utilise exactement le même calcul que l'application.

Si une matière n'a pas de ligne explicite dans `groupes_matieres`, le moteur utilise par sécurité la matière elle-même et son coefficient BAC. Le fichier fourni contient toutefois les correspondances explicites pour toutes les matières actuellement exploitables.

## Reconstruction obligatoire

Les anciennes distributions doivent être remplacées :

```bash
python -u build_population_et_distributions_db.py \
  --params parametres_simulateur_inphb.xlsx \
  --db population_inphb.db \
  --db-distributions population_inphb_distributions.db \
  --n 200000 \
  --batch-size 5000 \
  --sigma-bac 2.4 \
  --part-profils-forts 0.60
```
