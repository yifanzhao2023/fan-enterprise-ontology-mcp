MATCH (k:Contract)
RETURN k.hasStatus AS status, count(k) AS contract_count
ORDER BY contract_count DESC
LIMIT 50
