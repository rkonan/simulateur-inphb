# Refonte éligibilité et statistiques par série

## Principes appliqués

1. L'onglet `eligibilite_inphb` est l'unique source de vérité pour les filières autorisées par série.
2. L'onglet `coefficients_inphb` sert uniquement au calcul du score.
3. La base SQLite sert uniquement à l'analyse comparative.
4. Une analyse comparative n'est calculée que si une distribution existe pour le couple `(serie, filiere)`.
5. Une ancienne base agrégée uniquement par filière est déclarée indisponible afin d'éviter le mélange des séries.

## Modification du fichier Excel

Une colonne `profil_coefficients` a été ajoutée dans `eligibilite_inphb`.
Elle permet à plusieurs filières de partager la même formule :

- `GMC`, `GRH`, `GSC` utilisent le profil `GAE` ;
- `CCA`, `BFA` utilisent le profil `FCA` ;
- les autres filières utilisent leur propre profil.

`LSA` reste éligible dans l'Excel, mais son profil est vide car aucune formule de score n'est actuellement paramétrée. L'application l'indique sans bloquer les autres filières.

## Régénération obligatoire de la base statistique

La table `distributions_scores` contient désormais :

```sql
serie, filiere, score_arrondi, effectif
```

Régénérer la base avec :

```bash
python build_population_et_distributions_db.py \
  --params parametres_simulateur_inphb.xlsx \
  --db population_inphb.db \
  --db-distributions population_inphb_distributions.db
```

L'application continue de fonctionner sans base statistique : elle affiche les scores dossier, puis signale que l'analyse comparative n'est pas disponible.


## Formules et dénominateurs automatiques

L'onglet `coefficients_inphb` ne contient plus de colonnes `denominateur` ni `formule`.
Pour chaque couple filière/série, le moteur sélectionne les matières applicables puis :

- calcule le dénominateur comme la somme de `coefficient_dossier` ;
- génère le libellé de la formule à partir des matières et coefficients ;
- utilise exactement la même logique dans l'application et lors de la reconstruction de la base statistique.

Il suffit donc d'ajouter, retirer ou modifier une ligne matière/coefficient. Aucun total ni texte de formule n'est à maintenir manuellement.
