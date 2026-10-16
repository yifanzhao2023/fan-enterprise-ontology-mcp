"""Compile TBox.ttl + shapes.ttl into the flat query-side projection.

The projection is deliberately NOT the ontology: it carries only what an agent
needs to write Cypher — names, kinds, ranges, cardinality, and Core constraints.
OWL axioms never enter it and it is never fed to a reasoner.
"""

from pathlib import Path
from typing import Any

from rdflib import OWL, RDF, RDFS, Graph, Namespace

SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

CORE_KINDS = ("minCount", "maxCount", "datatype", "class")


def _local(uri: Any) -> str:
    return str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def build_projection(tbox_path: Path, shapes_path: Path) -> dict[str, Any]:
    tbox = Graph()
    tbox.parse(tbox_path, format="turtle")
    shapes = Graph()
    shapes.parse(shapes_path, format="turtle")

    classes: dict[str, dict[str, Any]] = {}
    for cls in tbox.subjects(RDF.type, OWL.Class):
        name = _local(cls)
        labels = sorted(str(l) for l in tbox.objects(cls, RDFS.label))
        superclasses = sorted(_local(s) for s in tbox.objects(cls, RDFS.subClassOf))
        classes[name] = {
            "iri": str(cls),
            "name": name,
            "labels": labels,
            "superclasses": superclasses,
            "properties": [],
            "relations_out": [],
            "relations_in": [],
        }

    properties: list[dict[str, Any]] = []
    for prop in list(tbox.subjects(RDF.type, OWL.DatatypeProperty)) + list(
        tbox.subjects(RDF.type, OWL.ObjectProperty)
    ):
        kind = "datatype" if (prop, RDF.type, OWL.DatatypeProperty) in tbox else "object"
        name = _local(prop)
        domains = sorted(_local(d) for d in tbox.objects(prop, RDFS.domain))
        ranges = sorted(_local(r) for r in tbox.objects(prop, RDFS.range))
        properties.append({
            "iri": str(prop),
            "name": name,
            "kind": kind,
            "domain": domains[0] if domains else None,
            "range": ranges[0] if ranges else None,
        })

    cardinality = _cardinality(shapes)

    for entry in properties:
        dom = entry["domain"]
        if dom not in classes:
            continue
        card = cardinality.get((dom, entry["name"]), {})
        if entry["kind"] == "datatype":
            classes[dom]["properties"].append({
                "iri": entry["iri"],
                "name": entry["name"],
                "kind": "datatype",
                "range": entry["range"],
                "min": card.get("min"),
                "max": card.get("max"),
            })
        else:
            classes[dom]["relations_out"].append({
                "iri": entry["iri"],
                "name": entry["name"],
                "target": entry["range"],
                "min": card.get("min"),
                "max": card.get("max"),
            })
            if entry["range"] in classes:
                classes[entry["range"]]["relations_in"].append({
                    "iri": entry["iri"],
                    "name": entry["name"],
                    "source": dom,
                    "min": card.get("min"),
                    "max": card.get("max"),
                })

    constraints: dict[str, list[dict[str, Any]]] = {}
    for shape in shapes.subjects(RDF.type, SH.NodeShape):
        target = shapes.value(shape, SH.targetClass)
        if target is None:
            continue
        entries = []
        for prop_shape in shapes.objects(shape, SH.property):
            path = shapes.value(prop_shape, SH.path)
            if path is None:
                continue
            pname = _local(path)
            for kind in CORE_KINDS:
                pred = SH[kind]
                for value in shapes.objects(prop_shape, pred):
                    entries.append({
                        "path": pname,
                        "kind": kind,
                        "value": str(value),
                    })
        if entries:
            constraints[_local(target)] = sorted(entries, key=lambda e: (e["path"], e["kind"]))

    return {
        "version": 1,
        "classes": sorted(classes.values(), key=lambda c: c["name"]),
        "properties": sorted(properties, key=lambda p: p["name"]),
        "constraints": dict(sorted(constraints.items())),
    }


def _cardinality(shapes: Graph) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for shape in shapes.subjects(RDF.type, SH.NodeShape):
        target = shapes.value(shape, SH.targetClass)
        if target is None:
            continue
        for prop_shape in shapes.objects(shape, SH.property):
            path = shapes.value(prop_shape, SH.path)
            if path is None:
                continue
            key = (_local(target), _local(path))
            card: dict[str, Any] = {}
            mn = shapes.value(prop_shape, SH.minCount)
            mx = shapes.value(prop_shape, SH.maxCount)
            if mn is not None:
                card["min"] = int(mn)
            if mx is not None:
                card["max"] = int(mx)
            out[key] = card
    return out
