"""Load an ABox Turtle file into the store.

The sample ontology ships a sample ABox so the demo and the CI suite have real
data to run against. The loader hands the parsed triples to the same
representation writer the MCP upsert tools use, so a seeded graph and a
tool-written graph are indistinguishable — and n10s-compatible either way.
"""

from pathlib import Path

from rdflib import Graph, Literal, RDF

from .ports import Entity, Relation, WriteBatch


def _local(uri: object) -> str:
    return str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def load_abox(ttl_path: Path) -> WriteBatch:
    """Parse ABox Turtle into a WriteBatch.

    rdf:type becomes the entity's types, literals become datatype properties and
    URI objects become relations — the same three-way split the write path uses.
    """
    g = Graph()
    g.parse(ttl_path, format="turtle")

    types: dict[str, list[str]] = {}
    props: dict[str, dict[str, object]] = {}
    rels: list[Relation] = []

    for subj, pred, obj in g:
        iri = str(subj)
        if pred == RDF.type:
            types.setdefault(iri, []).append(str(obj))
        elif isinstance(obj, Literal):
            props.setdefault(iri, {})[_local(pred)] = obj.toPython()
        else:
            rels.append(Relation(iri, str(pred), str(obj)))

    entities = [
        Entity(iri=iri, types=tuple(t), properties=props.get(iri, {}))
        for iri, t in sorted(types.items())
    ]
    for iri in sorted(props):
        if iri not in types:
            entities.append(Entity(iri=iri, types=(), properties=props[iri]))

    return WriteBatch(entities=entities, relations=rels)
