MATCH (e:Employee {uri: $employee_iri})<-[:hasOwner]-(w:WorkOrder)
RETURN w.uri AS work_order_iri
ORDER BY work_order_iri
LIMIT 50
