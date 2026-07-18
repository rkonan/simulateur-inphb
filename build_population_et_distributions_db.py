from __future__ import annotations

import argparse
import math
import sqlite3
import time
from pathlib import Path

from simulateur_core import (
    charger_parametres,
    generer_population_unique_par_lots,
    initialiser_db_population,
    inserer_lot_population,
)


def formater_duree(secondes: float) -> str:
    """Formate une durée en HH:MM:SS ou MM:SS."""
    secondes_entieres = max(0, int(round(secondes)))
    heures, reste = divmod(secondes_entieres, 3600)
    minutes, secondes = divmod(reste, 60)

    if heures:
        return f"{heures:02d}:{minutes:02d}:{secondes:02d}"
    return f"{minutes:02d}:{secondes:02d}"


def taille_fichier_mo(path: Path) -> float:
    """Retourne la taille d'un fichier en mégaoctets."""
    if not path.exists():
        return 0.0
    return path.stat().st_size / (1024 * 1024)


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
) -> dict[str, int | float]:
    """Agrège les scores de la base population sans les charger en mémoire."""
    db_population = Path(db_population)
    db_distributions = Path(db_distributions)
    debut = time.perf_counter()

    print("\n=== Construction de la base des distributions ===", flush=True)
    print(
        f"Source : {db_population.resolve()} "
        f"({taille_fichier_mo(db_population):,.1f} Mo)",
        flush=True,
    )

    print("[1/5] Initialisation de la base cible...", flush=True)
    initialiser_db_distributions(db_distributions)

    with sqlite3.connect(db_distributions) as con:
        print("[2/5] Agrégation des scores par série, filière et score...", flush=True)
        debut_agregation = time.perf_counter()

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
        con.commit()
        duree_agregation = time.perf_counter() - debut_agregation
        print(
            f"      Agrégation terminée en {formater_duree(duree_agregation)}.",
            flush=True,
        )

        print("[3/5] Calcul des statistiques de contrôle...", flush=True)
        nb_lignes_distribution = int(
            con.execute("SELECT COUNT(*) FROM distributions_scores").fetchone()[0]
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

        print(
            "      "
            f"{nb_lignes_distribution:,} lignes, "
            f"{nb_scores_agreges:,} scores, "
            f"{nb_filieres:,} filières, "
            f"{nb_couples:,} couples série/filière.",
            flush=True,
        )

        print("[4/5] Enregistrement des métadonnées...", flush=True)
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
            [(key, str(value)) for key, value in metadata_distribution.items()],
        )
        con.commit()
        con.execute("DETACH DATABASE population_db")

        print("[5/5] Compactage SQLite (VACUUM)...", flush=True)
        debut_vacuum = time.perf_counter()
        con.execute("VACUUM")
        duree_vacuum = time.perf_counter() - debut_vacuum
        print(
            f"      Compactage terminé en {formater_duree(duree_vacuum)}.",
            flush=True,
        )

    duree_totale = time.perf_counter() - debut
    taille_finale = taille_fichier_mo(db_distributions)

    print(
        f"Base distributions créée en {formater_duree(duree_totale)} : "
        f"{db_distributions.resolve()} ({taille_finale:,.1f} Mo)",
        flush=True,
    )

    return {
        "nb_lignes": nb_lignes_distribution,
        "nb_scores": nb_scores_agreges,
        "nb_filieres": nb_filieres,
        "nb_couples": nb_couples,
        "duree_secondes": duree_totale,
        "taille_mo": taille_finale,
    }


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
        default="population_inphb_distributions.db",
        help="Base synthétique des distributions de scores.",
    )
    parser.add_argument("--n", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--sigma-bac", type=float, default=2.4)
    parser.add_argument("--version-modele", default="v6_groupes_matieres")
    parser.add_argument(
        "--part-profils-forts",
        type=float,
        default=0.60,
        help="Part des profils calibrés sur les 900 admis. Défaut : 0.60.",
    )
    args = parser.parse_args()

    if args.n <= 0:
        parser.error("--n doit être strictement positif.")
    if args.batch_size <= 0:
        parser.error("--batch-size doit être strictement positif.")
    if not 0 < args.part_profils_forts < 1:
        parser.error("--part-profils-forts doit être strictement compris entre 0 et 1.")

    db_population = Path(args.db)
    db_distributions = Path(args.db_distributions)
    nb_lots = math.ceil(args.n / args.batch_size)
    debut_total = time.perf_counter()

    print("=== Génération de la population INP-HB ===", flush=True)
    print(f"Paramètres        : {Path(args.params).resolve()}", flush=True)
    print(f"Population cible  : {args.n:,} candidats", flush=True)
    print(f"Taille des lots   : {args.batch_size:,}", flush=True)
    print(f"Nombre de lots    : {nb_lots:,}", flush=True)
    print(f"Seed              : {args.seed}", flush=True)
    print(f"Sigma BAC         : {args.sigma_bac}", flush=True)
    print(f"Profils forts     : {args.part_profils_forts:.0%}", flush=True)
    print(f"Base population   : {db_population.resolve()}", flush=True)
    print(f"Base distributions: {db_distributions.resolve()}\n", flush=True)

    print("Chargement des paramètres...", flush=True)
    params = charger_parametres(args.params)

    print("Initialisation de la base population...", flush=True)
    initialiser_db_population(db_population)

    nc = nn = ns = 0
    debut_generation = time.perf_counter()

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
        debut_lot = time.perf_counter()

        inserer_lot_population(
            db_population,
            candidats,
            notes,
            scores,
        )

        nb_candidats_lot = len(candidats)
        nc += nb_candidats_lot
        nn += len(notes)
        ns += len(scores)

        duree_generation = time.perf_counter() - debut_generation
        duree_lot = time.perf_counter() - debut_lot
        debit_global = nc / duree_generation if duree_generation > 0 else 0.0
        debit_lot = nb_candidats_lot / duree_lot if duree_lot > 0 else 0.0
        progression = min(nc / args.n, 1.0)
        candidats_restants = max(args.n - nc, 0)
        eta = candidats_restants / debit_global if debit_global > 0 else 0.0

        print(
            f"[{idx:>2}/{nb_lots}] "
            f"{progression * 100:6.2f}% | "
            f"{nc:,}/{args.n:,} candidats | "
            f"{nn:,} notes | "
            f"{ns:,} scores | "
            f"lot {debit_lot:,.0f} cand/s | "
            f"moy. {debit_global:,.0f} cand/s | "
            f"écoulé {formater_duree(duree_generation)} | "
            f"ETA {formater_duree(eta)} | "
            f"DB {taille_fichier_mo(db_population):,.1f} Mo",
            flush=True,
        )

    duree_generation = time.perf_counter() - debut_generation

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
        "duree_generation_secondes": round(duree_generation, 3),
    }

    print("\nEnregistrement des métadonnées de population...", flush=True)
    with sqlite3.connect(db_population) as con:
        con.executemany(
            """
            INSERT OR REPLACE INTO model_metadata(cle, valeur)
            VALUES (?, ?)
            """,
            [(key, str(value)) for key, value in metadata.items()],
        )
        con.commit()

    print(
        f"Base population créée en {formater_duree(duree_generation)} : "
        f"{db_population.resolve()} "
        f"({taille_fichier_mo(db_population):,.1f} Mo)",
        flush=True,
    )

    stats_distribution = construire_db_distributions(
        db_population=db_population,
        db_distributions=db_distributions,
        metadata=metadata,
    )

    duree_totale = time.perf_counter() - debut_total

    print("\n=== Traitement terminé ===", flush=True)
    print(f"Durée totale        : {formater_duree(duree_totale)}", flush=True)
    print(f"Candidats générés   : {nc:,}", flush=True)
    print(f"Notes générées      : {nn:,}", flush=True)
    print(f"Scores générés      : {ns:,}", flush=True)
    print(f"Lignes distribution : {int(stats_distribution['nb_lignes']):,}", flush=True)
    print(f"Filières statistiques: {int(stats_distribution['nb_filieres']):,}", flush=True)
    print(f"Couples série/filière: {int(stats_distribution['nb_couples']):,}", flush=True)


if __name__ == "__main__":
    main()
