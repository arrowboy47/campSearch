#!/usr/bin/env python3
"""Generate and store semantic embeddings for campsite documents.

Composes a campsite document from structured attributes (name, forest, terrain,
activities, water feature, toilet type, free/paid, reservation type, elevation),
then embeds it using a remote embedding service. Only re-embeds when the document
text changes (detected via SHA-256 hash).

Documents are composited because 39% of campsites have NULL overview, so the
feature would be semantically unreachable for them if overview were the basis.

    python scripts/embed_campsites.py --dry-run               # show what would happen
    python scripts/embed_campsites.py --commit                # write to database
    python scripts/embed_campsites.py --limit 10 --commit
    python scripts/embed_campsites.py --source manual --commit

Dry-run is the default. It shows how many documents would be embedded, how many
are unchanged, and prints 2-3 full documents so a human can judge text quality.
Dry-run writes nothing to the database (no scrape_runs row, no embeddings).
"""

import sys
import os
import argparse
import hashlib

from forests import forest_label

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embeddings import EmbeddingProvider, EmbeddingError
from scripts._pipeline import get_conn, scrape_run


BATCH_SIZE = 32
MODEL_NAME = "nomic-embed-text"


OVERVIEW_CHAR_LIMIT = 1500


def compose_document(row):
    """Compose a readable document from a campsite row.

    Generates prose from structured attributes so that campsites with NULL
    overview still produce meaningful embeddings.

    Args:
        row: Tuple of (id, name, forest_name, terrain, activities,
                       water_feature, toilet_type, is_free, reservation_type,
                       elevation_ft, overview)

    Returns:
        A string document to embed.
    """
    cid, name, forest, terrain, activities, water, toilet, is_free, resv_type, elev, overview = row

    parts = []

    # Name and location
    if name:
        parts.append(f"Campsite: {name}")
    if forest:
        # forest_name is stored as a slug ("klamath"), and the embedding model
        # was trained on prose, so give it the readable name.
        parts.append(f"Located in {forest_label(forest)}")

    # Landscape characteristics
    if terrain:
        parts.append(f"Terrain: {terrain}")
    if elev:
        if elev < 1500:
            elev_band = "lowland"
        elif elev < 4000:
            elev_band = "foothills"
        elif elev < 7000:
            elev_band = "mountain"
        else:
            elev_band = "alpine"
        parts.append(f"Elevation: {elev} feet ({elev_band})")

    # Amenities
    if water:
        parts.append(f"Water feature: {water}")
    if toilet:
        parts.append(f"Toilet type: {toilet}")
    if is_free is not None:
        fee_str = "Free camping" if is_free else "Paid camping"
        parts.append(fee_str)
    if resv_type:
        parts.append(f"Reservation type: {resv_type}")

    # Activities
    if activities:
        if isinstance(activities, list):
            act_str = ", ".join(activities)
        else:
            act_str = str(activities)
        parts.append(f"Activities available: {act_str}")

    # Overview if present
    if overview:
        # Bound the overview. Documents with one run to a median of about 1900
        # characters and up to 8200, against about 280 for those without, and a
        # single embedding vector averages whatever it is given. Left unbounded
        # the long ones dilute the campsite's own signal across general prose
        # about the forest, and the longest risk silent truncation at the
        # model's context limit. Cutting on a sentence boundary keeps the text
        # readable, which is what the model was trained on.
        trimmed = overview.strip()
        if len(trimmed) > OVERVIEW_CHAR_LIMIT:
            cut = trimmed[:OVERVIEW_CHAR_LIMIT]
            stop = max(cut.rfind(". "), cut.rfind(".\n"))
            trimmed = cut[: stop + 1] if stop > OVERVIEW_CHAR_LIMIT // 2 else cut
        parts.append(f"Description: {trimmed}")

    return ". ".join(parts) + "."


def hash_text(text):
    """Compute SHA-256 hash of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would happen (default)"
    )
    p.add_argument(
        "--commit",
        dest="dry_run",
        action="store_false",
        help="Write embeddings to database"
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only N campsites"
    )
    p.add_argument(
        "--source",
        default="embed",
        help="Source name for scrape_runs row (default: embed)"
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Set up embedding provider
    try:
        provider = EmbeddingProvider()
    except Exception as e:
        print(f"Error initializing embedding provider: {e}", file=sys.stderr)
        sys.exit(1)

    with get_conn() as conn:
        # Fetch all campsites with their attributes
        sel = conn.cursor()
        sel.execute(
            """
            SELECT
                c.id, c.name, c.forest_name, c.terrain, a.activities,
                a.water_feature, a.toilet_type, c.is_free, c.reservation_type,
                c.elevation_ft, c.overview
            FROM campsites c
            LEFT JOIN amenities a ON a.campsite_id = c.id
            ORDER BY c.id
            """
        )
        all_rows = sel.fetchall()
        sel.close()

        if args.limit:
            all_rows = all_rows[:args.limit]

        # Fetch existing embeddings for hash comparison
        sel = conn.cursor()
        sel.execute("SELECT campsite_id, source_text_hash FROM campsite_embeddings")
        existing = {cid: hash_ for cid, hash_ in sel.fetchall()}
        sel.close()

        # Decide which campsites to embed
        to_embed = []
        unchanged = 0

        for row in all_rows:
            doc = compose_document(row)
            doc_hash = hash_text(doc)
            cid = row[0]

            if cid in existing and existing[cid] == doc_hash:
                unchanged += 1
            else:
                to_embed.append((cid, doc, doc_hash))

        print(f"Total campsites: {len(all_rows)}")
        print(f"Need to embed: {len(to_embed)}")
        print(f"Unchanged: {unchanged}")

        if args.dry_run:
            print(f"\nDRY RUN MODE - Nothing will be written to the database")

            # Show sample documents
            print(f"\nSample documents ({min(3, len(to_embed))} of {len(to_embed)} to embed):")
            for i, (cid, doc, _) in enumerate(to_embed[:3]):
                print(f"\n  [{i+1}] Campsite {cid}:")
                for line in doc.split(". "):
                    if line:
                        print(f"      {line}.")
            return

        # Commit mode: embed and write to database
        written = 0
        failed_batches = 0
        print(f"\nEmbedding {len(to_embed)} documents...")

        try:
            # Embed and write one batch at a time, committing as we go.
            #
            # The first version embedded all 2630 documents into memory and
            # committed once at the end. On a run of any length that means an
            # interruption loses everything, and it makes the hash-based skip
            # useless for resuming, since nothing was ever written to skip
            # against. Committing per batch turns a failed run into a partial
            # one that the next run finishes.
            upd = conn.cursor()

            for i in range(0, len(to_embed), BATCH_SIZE):
                chunk = to_embed[i:i + BATCH_SIZE]
                batch_no = i // BATCH_SIZE + 1
                try:
                    vectors = provider.embed([doc for _, doc, _ in chunk])
                except EmbeddingError as e:
                    # One bad batch should not discard the batches already
                    # committed, nor abandon the rest of the corpus.
                    failed_batches += 1
                    print("  batch %d failed: %s" % (batch_no, e), file=sys.stderr)
                    continue

                for (cid, doc, doc_hash), embedding in zip(chunk, vectors):
                    upd.execute(
                        """
                        INSERT INTO campsite_embeddings
                            (campsite_id, embedding, source_text_hash, model, updated_at)
                        VALUES (%s, %s, %s, %s, now())
                        ON CONFLICT (campsite_id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            source_text_hash = EXCLUDED.source_text_hash,
                            model = EXCLUDED.model,
                            updated_at = now()
                        """,
                        (cid, embedding, doc_hash, MODEL_NAME)
                    )
                conn.commit()
                written += len(chunk)
                if batch_no % 10 == 0 or i + BATCH_SIZE >= len(to_embed):
                    print("  %d/%d embedded" % (written, len(to_embed)))

            upd.close()
            if failed_batches:
                print("  %d batch(es) failed; re-run to fill the gaps"
                      % failed_batches, file=sys.stderr)

            print(f"Wrote {written} embeddings")

        except EmbeddingError as e:
            print(f"Embedding failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Log the run (dry runs write nothing at all, not even this row)
    if not args.dry_run:
        with scrape_run(args.source) as run:
            run.seen = len(all_rows)
            # What was actually written, not what was intended. Reporting
            # len(to_embed) would claim success for batches that failed.
            run.upserted = written
            run.errors = failed_batches


if __name__ == "__main__":
    main()
