MATCH (d:Department {uri: $department_iri})<-[:worksFor]-(e:Employee)
RETURN e.uri AS employee_iri
ORDER BY employee_iri
LIMIT 50
