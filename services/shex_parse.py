"""Reading ShExer's compact ShEx output.

Three things parse this schema — the Mermaid diagram, the rdf-config YAML and
the coverage bars — and each had grown its own regex for it. All three were
wrong in the same two ways, because ShExer's output has two features that a
casual regex does not survive:

  * every constraint carries a comment, and those comments contain braces:
        <https://schema.org/name>  <...#string>  ?;
                 # 46.78 % obj: <...#string>. Cardinality: {1}
    so a shape body read as "{ up to the next }" stops inside the first comment,
    losing the rest of the shape and leaving its tail to be mistaken for another;

  * '#' appears inside URIs (XMLSchema#string, rdf-syntax-ns#type), so stripping
    comments with /#.*$/ truncates the URI and renames the datatype.

Parsing it once, here, is the point of this module.
"""
import re


def strip_comments(text: str) -> str:
    """Remove ShEx comments, leaving '#' that is part of a URI or literal alone."""
    out = []
    for line in text.splitlines():
        angle = 0
        quoted = False
        kept = []
        for ch in line:
            if ch == '"' and not angle:
                quoted = not quoted
            elif ch == "<" and not quoted:
                angle += 1
            elif ch == ">" and not quoted and angle:
                angle -= 1
            elif ch == "#" and not angle and not quoted:
                break                     # a real comment: drop the rest of the line
            kept.append(ch)
        out.append("".join(kept).rstrip())
    return "\n".join(out)


def shape_blocks(text: str):
    """Yield (name, body) for each shape, matching braces rather than guessing.

    Expects comment-free text: run strip_comments first, or the braces inside
    ShExer's cardinality comments will be counted as structure.
    """
    i = 0
    while True:
        open_at = text.find("{", i)
        if open_at == -1:
            return
        head = text[i:open_at].strip().splitlines()
        name = head[-1].strip() if head else ""
        depth, j = 1, open_at + 1
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        if depth:
            return                        # unbalanced; nothing sensible remains
        if name and not name.upper().startswith(("PREFIX", "BASE")):
            yield name, text[open_at + 1: j - 1]
        i = j


def local_name(token: str) -> str:
    """The readable tail of a URI or prefixed name.

    Splits on the LAST '#' or '/', so XMLSchema#string gives "string" rather
    than "XMLSchema" — which is what a caller that stripped the fragment as a
    comment first ends up with.
    """
    token = token.strip().strip("<>")
    m = re.search(r'[#/]([^#/]+)$', token)
    if m:
        token = m.group(1)
    elif ":" in token:
        token = token.split(":")[-1]
    return re.sub(r"\W+", "_", token).strip("_")


def constraints(body: str):
    """Yield (predicate, type_token, cardinality) for each constraint in a body.

    Percentages are not returned: they live in the comments this module strips,
    and the callers that want them read them separately.
    """
    for line in body.splitlines():
        line = line.strip().rstrip(";").strip()
        if not line:
            continue
        if "rdf-syntax-ns#type" in line or line.startswith("a "):
            continue                      # the type constraint is the shape itself
        parts = line.split()
        if len(parts) < 2:
            continue
        card = ""
        m = re.search(r"(\+|\?|\*|\{[^}]+\})\s*$", line)
        if m:
            c = m.group(1)
            card = {"*": "0..*", "?": "0..1", "+": "1..*"}.get(c, c.strip("{}"))
        yield parts[0], parts[1], card
