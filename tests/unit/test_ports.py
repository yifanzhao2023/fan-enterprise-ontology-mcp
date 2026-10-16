from feomcp.ports import Entity, Relation, WriteBatch, WriteReceipt


def test_batch_size_counts_entities_and_relations():
    b = WriteBatch(
        entities=[Entity(iri="https://example.org/id/a", types=("https://example.org/onto#Customer",))],
        relations=[Relation("https://example.org/id/a", "https://example.org/onto#worksFor",
                            "https://example.org/id/b")],
    )
    assert b.size() == 2


def test_empty_batch_is_empty():
    assert WriteBatch().size() == 0


def test_receipt_defaults_are_safe():
    r = WriteReceipt(written_iris=(), skipped=(), validation={"violations": []}, applied=True)
    assert r.applied is True
    assert r.written_iris == ()
