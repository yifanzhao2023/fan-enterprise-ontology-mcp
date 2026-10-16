MATCH (k:Contract {uri: $contract_iri})
RETURN k.hasAmount AS amount
LIMIT 1
