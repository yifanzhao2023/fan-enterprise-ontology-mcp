MATCH (c:Customer {uri: $customer_iri})<-[:belongsToCustomer]-(k:Contract)
WHERE toFloat(k.hasAmount) > toFloat($min_amount)
RETURN k.uri AS contract_iri, toFloat(k.hasAmount) AS amount
ORDER BY amount DESC
LIMIT 50
