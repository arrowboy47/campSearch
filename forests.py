"""Display labels for the forest_name slugs stored on campsites.

The DB keeps forests as URL slugs (``stanislaus``, ``shasta-trinity`` ...) because
that's what the fs.usda.gov scrape URLs use and what search matches against.
Templates should show a real name instead. Only ~20 slugs exist, so an explicit
map is simplest and avoids mangling names like "Los Padres" or "Six Rivers".
"""

FOREST_LABELS = {
    "angeles": "Angeles National Forest",
    "cleveland": "Cleveland National Forest",
    "eldorado": "Eldorado National Forest",
    "humboldt-toiyabe": "Humboldt-Toiyabe National Forest",
    "inyo": "Inyo National Forest",
    "klamath": "Klamath National Forest",
    "laketahoebasin": "Lake Tahoe Basin Management Unit",
    "lassen": "Lassen National Forest",
    "lospadres": "Los Padres National Forest",
    "mendocino": "Mendocino National Forest",
    "modoc": "Modoc National Forest",
    "plumas": "Plumas National Forest",
    "rogue-siskiyou": "Rogue River-Siskiyou National Forest",
    "sanbernardino": "San Bernardino National Forest",
    "sequoia": "Sequoia National Forest",
    "shasta-trinity": "Shasta-Trinity National Forest",
    "sierra": "Sierra National Forest",
    "sixrivers": "Six Rivers National Forest",
    "stanislaus": "Stanislaus National Forest",
    "tahoe": "Tahoe National Forest",
}


def forest_label(value):
    """Pretty name for a forest slug. Unknown / already-pretty values, and
    non-slug text (spaces, uppercase), are returned unchanged."""
    if not value:
        return value
    key = value.strip().lower()
    if key in FOREST_LABELS:
        return FOREST_LABELS[key]
    if " " in value or value != key:  # looks like a real name already
        return value
    return key.replace("-", " ").title()
