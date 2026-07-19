#!/bin/ksh

set -e

PARAMS="parametres_simulateur_inphb.xlsx"
DB="population_inphb.db"
DB_DISTRIBUTION="population_inphb_distributions.db"
N=2000
SEED=123
BATCH_SIZE=10000      # À adapter
PART_PROFILS_FORTS=0.6

python build_population_et_distributions_db.py \
    --params "$PARAMS" \
    --db "$DB" \
    --db-distribution "$DB_DISTRIBUTION" \
    --n "$N" \
    --seed "$SEED" \
    --batch-size "$BATCH_SIZE" \
    --part-profils-forts "$PART_PROFILS_FORTS"