MATCH (e:ChangeEvent {subject: $contract_iri})
RETURN e.at AS at, e.actor AS actor, e.predicate AS predicate,
       e.old_value AS old_value, e.new_value AS new_value
ORDER BY at DESC, e.uri DESC
LIMIT 50
