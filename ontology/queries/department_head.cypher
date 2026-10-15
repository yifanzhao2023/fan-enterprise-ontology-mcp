MATCH (d:Department {uri: $department_iri})-[:hasHead]->(e:Employee)
RETURN e.uri AS employee_iri
LIMIT 10
