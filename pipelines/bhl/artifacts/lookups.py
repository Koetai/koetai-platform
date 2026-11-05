"""FnML (RML function) helpers for the BHL mapping.

Registered as Python UDFs in bhl-mapping.yarrrml.yml's function mappings.
Kept separate from the mapping itself: these are small, generic string
transforms, not BHL-specific modeling decisions — the mapping decides *when*
to call them, this module decides *how* they work.
"""


def iri_segment(value: str, preserve_slash: bool = False) -> str:
    """Percent-encode a value for safe embedding as one IRI path segment.

    The one bug this function exists to prevent from recurring: the original
    BHL materialization used `urllib.parse.quote(value, safe="")` unconditionally,
    which happens to be exactly right for a DOI's *reserved characters* but wrong
    for its *structural* '/' — DOIs are always of the form "prefix/suffix", and
    escaping that slash to %2F produced a URL that no DOI resolver, and nothing
    that treats a DOI as a URL path, will recognize. `preserve_slash=True` is
    for exactly that case; every other identifier segment should leave it False,
    since an *unexpected* literal slash there would silently merge two IRI path
    segments into one.
    """
    from urllib.parse import quote
    return quote(value.strip(), safe="/" if preserve_slash else "")


# Namespaces from {creator,title,part}identifier.txt's IdentifierName column
# that resolve to a real, dereferenceable URI. Deliberately not exhaustive —
# BioStor's and TL2's current URL schemes aren't something this pipeline can
# verify with confidence, so identifiers in those namespaces (and any other
# not listed here) fall back to a literal CURIE via dcterms:identifier,
# exactly as creator identifiers already did for the namespaces nobody had
# resolved yet. Extending coverage later means adding a line here, not
# touching the mapping.
_IDENTIFIER_URI_TEMPLATES = {
    "VIAF":     "http://viaf.org/viaf/{}",
    "Wikidata": "http://www.wikidata.org/entity/{}",
    "OCLC":     "http://www.worldcat.org/oclc/{}",
    "DLC":      "http://id.loc.gov/authorities/names/{}",
    "SNAC ARK": "https://snaccooperative.org/ark:/99166/{}",
}


def identifier_to_uri(identifier_name: str, identifier_value: str) -> str:
    """Resolve an (IdentifierName, IdentifierValue) pair to a real URI, or ''.

    '' (not None — RML function values must be strings) signals "no known
    pattern for this namespace"; the mapping routes that case to a literal
    dcterms:identifier CURIE instead of a manufactured, possibly-wrong URI.
    """
    template = _IDENTIFIER_URI_TEMPLATES.get((identifier_name or "").strip())
    if not template:
        return ""
    return template.format(iri_segment(identifier_value, preserve_slash=False))


def identifier_is_resolvable(identifier_name: str) -> bool:
    """True if identifier_to_uri() would return a real URI for this namespace.

    A boolean FnML function used as a YARRRML condition, so the mapping can
    route each identifier row to either the object-property (URI) mapping or
    the literal-CURIE fallback mapping without duplicating the resolution
    logic in two places.
    """
    return (identifier_name or "").strip() in _IDENTIFIER_URI_TEMPLATES


import re

_DOI_IRI_RE = re.compile(r"<(https://doi\.org/[^>]*)>")


def fix_doi_encoding(nt_line: str) -> str:
    """Unescape '%2F' back to '/' inside doi.org IRIs only.

    Confirmed empirically against this pipeline's own mapping: Morph-KGC
    percent-encodes *any* value substituted into an `~iri` template
    unconditionally — including a DOI's structural '/' — regardless of
    whether the mapping itself calls any encoding function. There is no
    YARRRML-level way to opt out of that per-template, so this is a targeted
    post-processing fix on the materialized N-Triples rather than something
    fixable in the mapping. The regex only matches inside a `<https://doi.org/...>`
    IRI reference, so an unrelated '%2F' elsewhere on the same line (e.g. in
    a different field) is never touched.
    """
    return _DOI_IRI_RE.sub(lambda m: f"<{m.group(1).replace('%2F', '/')}>", nt_line)


def curie(identifier_name: str, identifier_value: str) -> str:
    """The literal-CURIE fallback shape ("Namespace:Value") for an unresolved
    identifier — same shape the original script produced, kept for whatever
    still can't be resolved to a real URI."""
    return f"{(identifier_name or '').strip()}:{(identifier_value or '').strip()}"
