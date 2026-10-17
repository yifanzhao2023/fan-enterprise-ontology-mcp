MATCH (c:KeyContract)
WHERE ($archived IS NULL OR c.archived = $archived)
RETURN c.uri AS contract_iri, c.hasAmount AS amount
ORDER BY amount DESC, contract_iri
