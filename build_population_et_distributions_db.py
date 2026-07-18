from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from simulateur_core import (
    charger_parametres,
    generer_population_unique_par_lots,
    initialiser_db_population,
    inserer_lot_population,
)


def initialiser_db_distributions(path: str | Path) -> None:
    """Crée une base synthétique des distributions de scores par filière."""
    path = Path(path)

    if path.exists():
        path.unlink()

    with sqlite3.connect(path) as con:
        con.executescript(
            """
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;

            CREATE TABLE distributions_scores (
                serie TEXT NOT NULL,
                filiere TEXT NOT NULL,
                score_arrondi REAL NOT NULL,
                effectif INTEGER NOT NULL CHECK (effectif > 0),
                PRIMARY KEY (serie, filiere, score_arrondi)
            );

            CREATE TABLE model_metadata (
                cle TEXT PRIMARY KEY,
                valeur TEXT NOT NULL
            );

            CREATE INDEX idx_distributions_serie_filiere_score
            ON distributions_scores (serie, filiere, score_arrondi);
            """
        )
        con.commit()


def construire_db_distributions(
    db_population: str | Path,
    db_distributions: str | Path,
    metadata: dict[str, object],
) -> None:
    """Agrège les scores de la base population sans les charger en mémoire."""
    db_population = Path(db_population)
    db_distributions = Path(db_distributions)

    initialiser_db_distributions(db_distributions)

    with sqlite3.connect(db_distributions) as con:
        con.execute("ATTACH DATABASE ? AS population_db", (str(db_population),))

        con.execute(
            """
            INSERT INTO distributions_scores (
                serie,
                filiere,
                score_arrondi,
                effectif
            )
            SELECT
                c.serie,
                s.filiere,
                ROUND(s.score, 2) AS score_arrondi,
                COUNT(*) AS effectif
            FROM population_db.scores s
            JOIN population_db.candidats c
              ON c.candidate_id = s.candidate_id
            WHERE s.score IS NOT NULL
            GROUP BY
                c.serie,
                s.filiere,
                ROUND(s.score, 2)
            ORDER BY
                c.serie,
                s.filiere,
                score_arrondi
            """
        )

        nb_lignes_distribution = int(
            con.execute(
                "SELECT COUNT(*) FROM distributions_scores"
            ).fetchone()[0]
        )
        nb_scores_agreges = int(
            con.execute(
                "SELECT COALESCE(SUM(effectif), 0) FROM distributions_scores"
            ).fetchone()[0]
        )
        nb_filieres = int(
            con.execute(
                "SELECT COUNT(DISTINCT filiere) FROM distributions_scores"
            ).fetchone()[0]
        )
        nb_couples = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT serie, filiere
                    FROM distributions_scores
                )
                """
            ).fetchone()[0]
        )

        metadata_distribution = {
            **metadata,
            "nombre_lignes_distribution": nb_lignes_distribution,
            "nombre_scores_agreges": nb_scores_agreges,
            "nombre_filieres": nb_filieres,
            "nombre_couples_serie_filiere": nb_couples,
            "precision_score_decimales": 2,
            "source_db_population": db_population.name,
        }

        con.executemany(
            """
            INSERT OR REPLACE INTO model_metadata(cle, valeur)
            VALUES (?, ?)
            """,
            [
                (key, str(value))
                for key, value in metadata_distribution.items()
            ],
        )

        # Valide l'INSERT avant de détacher la base source.
        con.commit()
        con.execute("DETACH DATABASE population_db")

        # Réduit la taille finale du fichier après l'agrégation.
        con.execute("VACUUM")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Construit deux bases SQLite : "
            "1) la population fictive détaillée, "
            "2) la distribution synthétique des scores par couple série/filière."
        )
    )
    parser.add_argument(
        "--params",
        default="parametres_simulateur_inphb.xlsx",
    )
    parser.add_argument(
        "--db",
        default="population_inphb.db",
        help="Base détaillée de la population.",
    )
    parser.add_argument(
        "--db-distributions",
        default="distributions_scores_inphb.db",
        help="Base synthétique des distributions de scores.",
    )
    parser.add_argument("--n", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--sigma-bac", type=float, default=2.4)
    parser.add_argument("--version-modele", default="v3")
    parser.add_argument(
        "--part-profils-forts",
        type=float,
        default=0.60,
        help="Part des profils calibrés sur les 900 admis. Défaut : 0.60.",
    )
    args = parser.parse_args()

    db_population = Path(args.db)
    db_distributions = Path(args.db_distributions)

    params = charger_parametres(args.params)
    initialiser_db_population(db_population)

    nc = nn = ns = 0

    generateur = generer_population_unique_par_lots(
        params=params,
        n=args.n,
        seed=args.seed,
        taille_lot=args.batch_size,
        sigma_bac=args.sigma_bac,
        version_modele=args.version_modele,
        proportion_profils_forts=args.part_profils_forts,
    )

    for idx, (candidats, notes, scores) in enumerate(generateur, start=1):
        inserer_lot_population(
            db_population,
            candidats,
            notes,
            scores,
        )

        nc += len(candidats)
        nn += len(notes)
        ns += len(scores)

        print(
            f"Lot {idx}: "
            f"{nc:,}/{args.n:,} candidats, "
            f"{nn:,} notes, "
            f"{ns:,} scores"
        )

    metadata = {
        "nombre_candidats": nc,
        "nombre_notes": nn,
        "nombre_scores": ns,
        "seed": args.seed,
        "sigma_bac": args.sigma_bac,
        "version_modele": args.version_modele,
        "part_profils_forts": args.part_profils_forts,
        "part_extension": 1 - args.part_profils_forts,
        "seuil_admissibles": 1500,
    }

    with sqlite3.connect(db_population) as con:
        con.executemany(
            """
            INSERT OR REPLACE INTO model_metadata(cle, valeur)
            VALUES (?, ?)
            """,
            [
                (key, str(value))
                for key, value in metadata.items()
            ],
        )
        con.commit()

    print(f"Base population créée : {db_population.resolve()}")

    construire_db_distributions(
        db_population=db_population,
        db_distributions=db_distributions,
        metadata=metadata,
    )

    with sqlite3.connect(db_distributions) as con:
        nb_lignes, nb_scores, nb_filieres, nb_couples = con.execute(
            """
            SELECT
                COUNT(*) AS nb_lignes,
                COALESCE(SUM(effectif), 0) AS nb_scores,
                COUNT(DISTINCT filiere) AS nb_filieres,
                COUNT(DISTINCT serie || '|' || filiere) AS nb_couples
            FROM distributions_scores
            """
        ).fetchone()

    print(
        "Base distributions créée : "
        f"{db_distributions.resolve()} "
        f"({nb_lignes:,} lignes, "
        f"{nb_scores:,} scores agrégés, "
        f"{nb_filieres:,} filières, "
        f"{nb_couples:,} couples série/filière)"
    )


if __name__ == "__main__":
    main()
