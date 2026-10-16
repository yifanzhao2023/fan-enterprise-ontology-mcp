MATCH (k:Contract)
WHERE toFloat(k.hasAmount) > toFloat($min_amount) AND coalesce(k.archived, false) = false
RETURN k.uri AS contract_iri, toFloat(k.hasAmount) AS amount
ORDER BY amount DESC
LIMIT 50
