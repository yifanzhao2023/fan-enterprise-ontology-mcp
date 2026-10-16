MATCH (c:Customer {uri: $customer_iri})<-[:belongsToCustomer]-(k:Contract)
RETURN k.uri AS contract_iri, k.hasStatus AS status
ORDER BY contract_iri
LIMIT 50
