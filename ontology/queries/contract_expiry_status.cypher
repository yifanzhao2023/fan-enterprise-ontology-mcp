MATCH (c:Contract {uri: $contract_iri})
RETURN c.uri AS contract_iri,
       c.hasEndDate AS end_date,
       CASE
         WHEN c.hasEndDate IS NULL THEN 'unknown'
         WHEN date(c.hasEndDate) < date($as_of) THEN 'expired'
         ELSE 'active'
       END AS status
