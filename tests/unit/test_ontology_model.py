from rdflib import OWL, RDF, RDFS, Graph, URIRef, Namespace

EX = Namespace("https://example.org/onto#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
SH = Namespace("http://www.w3.org/ns/shacl#")


def load(path):
    g = Graph()
    g.parse(path, format="turtle")
    return g


def test_tbox_declares_six_classes():
    g = load("ontology/TBox.ttl")
    for name in [
        "BusinessDocument", "Contract", "Customer",
        "Department", "Employee", "WorkOrder",
    ]:
        assert (EX[name], RDF.type, OWL.Class) in g, name


def test_contract_is_a_business_document():
    g = load("ontology/TBox.ttl")
    assert (EX.Contract, RDFS.subClassOf, EX.BusinessDocument) in g


def test_eight_properties_are_declared():
    g = load("ontology/TBox.ttl")
    object_props = {"belongsToCustomer", "belongsToDepartment", "hasHead", "hasOwner", "worksFor"}
    data_props = {"hasAmount", "hasStatus", "archived"}
    for name in object_props:
        assert (EX[name], RDF.type, OWL.ObjectProperty) in g, name
    for name in data_props:
        assert (EX[name], RDF.type, OWL.DatatypeProperty) in g, name


def test_has_amount_domain_range():
    g = load("ontology/TBox.ttl")
    assert (EX.hasAmount, RDFS.domain, EX.Contract) in g
    assert (EX.hasAmount, RDFS.range, XSD.decimal) in g


def test_no_owl_dl_constructs_beyond_rl():
    g = load("ontology/TBox.ttl")
    banned = [OWL.unionOf, OWL.complementOf, OWL.oneOf, OWL.hasValue, OWL.disjointWith]
    for pred in banned:
        for _ in g.triples((None, pred, None)):
            raise AssertionError(f"forbidden OWL constructor: {pred}")


def test_shapes_cover_contract_amount_and_customer():
    g = load("ontology/shapes.ttl")
    shape = EX.ContractShape
    assert (shape, SH.targetClass, EX.Contract) in g
    paths = {str(p) for p in g.objects(shape, SH.property) for p in g.objects(p, SH.path)}
    assert str(EX.hasAmount) in paths
    assert str(EX.belongsToCustomer) in paths


def test_contract_amount_is_decimal_and_required():
    g = load("ontology/shapes.ttl")
    for prop in g.objects(EX.ContractShape, SH.property):
        if (prop, SH.path, EX.hasAmount) in g:
            assert (prop, SH.datatype, XSD.decimal) in g
            assert int(g.value(prop, SH.minCount)) == 1
            return
    raise AssertionError("hasAmount property shape not found")


def test_customer_shape_requires_a_name_property():
    """CustomerShape references onto:hasName; the TBox must declare it."""
    g = load("ontology/TBox.ttl")
    assert (EX.hasName, RDF.type, OWL.DatatypeProperty) in g
    assert (EX.hasName, RDFS.domain, EX.Customer) in g
    assert (EX.hasName, RDFS.range, XSD.string) in g


def test_key_contract_is_a_contract_subclass():
    g = load("ontology/TBox.ttl")
    assert (EX.KeyContract, RDF.type, OWL.Class) in g
    assert (EX.KeyContract, RDFS.subClassOf, EX.Contract) in g


def test_has_end_date_is_a_datetime_property_of_contract():
    g = load("ontology/TBox.ttl")
    assert (EX.hasEndDate, RDF.type, OWL.DatatypeProperty) in g
    assert (EX.hasEndDate, RDFS.domain, EX.Contract) in g
    assert (EX.hasEndDate, RDFS.range, XSD.dateTime) in g
