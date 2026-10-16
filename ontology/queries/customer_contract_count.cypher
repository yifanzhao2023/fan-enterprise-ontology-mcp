MATCH (c:Customer)<-[:belongsToCustomer]-(k:Contract)
RETURN c.uri AS customer_iri, count(k) AS contract_count
ORDER BY contract_count DESC
LIMIT 50
