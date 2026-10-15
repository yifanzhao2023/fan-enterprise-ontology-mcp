MATCH (d:Department {uri: $department_iri})<-[:belongsToDepartment]-(w:WorkOrder)
OPTIONAL MATCH (w)-[:hasOwner]->(e:Employee)
RETURN w.uri AS work_order_iri, e.uri AS owner_iri
ORDER BY work_order_iri
LIMIT 50
