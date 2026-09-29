GENERIC_TECHNOLOGY_TERMS = frozenset({"ai", "artificial intelligence"})


def is_generic_taxonomy_term(term, category):
    if category != "technology" or not isinstance(term, str):
        return False
    return term.strip().casefold() in GENERIC_TECHNOLOGY_TERMS