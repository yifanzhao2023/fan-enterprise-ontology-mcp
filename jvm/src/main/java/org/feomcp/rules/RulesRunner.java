package org.feomcp.rules;

import org.apache.jena.rdf.model.Model;
import org.apache.jena.rdf.model.ModelFactory;
import org.apache.jena.rdf.model.RDFNode;
import org.apache.jena.rdf.model.RDFNode;
import org.apache.jena.rdf.model.Statement;
import org.apache.jena.rdf.model.StmtIterator;
import org.apache.jena.reasoner.rulesys.GenericRuleReasoner;
import org.apache.jena.reasoner.rulesys.RDFSFBRuleReasoner;
import org.apache.jena.reasoner.rulesys.Rule;
import org.apache.jena.riot.Lang;
import org.apache.jena.riot.RDFDataMgr;

import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Turtle in, Turtle out. No Neo4j driver, no credentials, no network.
 *
 * The output is the DELTA: only triples the rules derive that are absent from
 * TBox + ABox. replace_derived strips last run's annotations by name, so a
 * restated input triple would cause an ABox label or edge to be deleted on the
 * next rebuild.
 *
 * The TBox is a reasoner input, not just a checksum: Jena's RDFS rules run as
 * the base so rdfs:subClassOf / subPropertyOf / domain / range fire. Business
 * conditions live in the rules directory. Nothing else in OWL is applied —
 * owl:TransitiveProperty, Symmetric, inverseOf and friends need their own rule.
 *
 * The OWL 2 RL profile gate is NOT here. Jena has no OWL2RLProfile class (that
 * is OWL API), so feomcp.validate.banned_owl_constructs owns it and runs before
 * this process is ever launched.
 */
public final class RulesRunner {

    public static void main(String[] args) {
        try {
            System.exit(run(args));
        } catch (Exception e) {
            System.err.println("rules-runner: " + e.getMessage());
            System.exit(4);
        }
    }

    static int run(String[] args) throws Exception {
        Path tbox = null, abox = null, rulesDir = null, out = null;
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--tbox" -> tbox = Path.of(args[++i]);
                case "--abox" -> abox = Path.of(args[++i]);
                case "--rules" -> rulesDir = Path.of(args[++i]);
                case "--out" -> out = Path.of(args[++i]);
                default -> {
                    System.err.println("unknown argument: " + args[i]);
                    return 2;
                }
            }
        }
        if (tbox == null || abox == null || rulesDir == null || out == null) {
            System.err.println("usage: rules-runner --tbox T --abox A --rules R --out O");
            return 2;
        }

        // RIOT, not Model.read: the legacy reader registry does not recognise
        // "TTL" as a language key and reports "Reader not found".
        Model data = ModelFactory.createDefaultModel();
        try {
            data.add(RDFDataMgr.loadModel(tbox.toString(), Lang.TURTLE));
        } catch (Exception e) {
            System.err.println("TBox is unusable: " + e.getMessage());
            return 3;
        }
        Model aboxModel = ModelFactory.createDefaultModel();
        if (Files.exists(abox) && Files.size(abox) > 0) {
            try {
                aboxModel = RDFDataMgr.loadModel(abox.toString(), Lang.TURTLE);
            } catch (Exception e) {
                System.err.println("ABox is unusable: " + e.getMessage());
                return 3;
            }
        }
        data.add(aboxModel);
        Set<String> aboxSubjects = new HashSet<>();
        aboxModel.listSubjects().forEachRemaining(r -> aboxSubjects.add(r.getURI()));

        List<Rule> rules = new ArrayList<>(RDFSFBRuleReasoner.loadRules());
        rules.addAll(loadRules(rulesDir));

        GenericRuleReasoner reasoner = new GenericRuleReasoner(rules);
        Model inferred = ModelFactory.createInfModel(reasoner, data);
        Model delta = ModelFactory.createDefaultModel();
        StmtIterator it = inferred.listStatements();
        while (it.hasNext()) {
            Statement st = it.next();
            if (data.contains(st)) {
                continue;
            }
            if (isBlank(st.getSubject()) || isBlank(st.getObject())) {
                continue; // blank nodes are not replayable across runs
            }
            if (!isInstanceFact(st, aboxSubjects)) {
                continue;
            }
            delta.add(st);
        }

        RDFDataMgr.write(Files.newOutputStream(out), delta, Lang.TURTLE);
        return 0;
    }

    private static boolean isBlank(RDFNode node) {
        return node.isAnon();
    }

    /**
     * Derived facts are annotations on ABox instances.
     *
     * The RDFS base rules close over the whole vocabulary — every class gets
     * `rdfs:subClassOf` itself, every term gets `a rdfs:Resource`, xsd:decimal
     * becomes a class. That is correct RDF and useless as a derived layer: the
     * writer would turn it into phantom nodes and into labels like `Resource`
     * and `Class` on real instances. Only facts about an ABox subject survive,
     * and only if they are not a bare typing of that subject into the RDF/RDFS/
     * XSD/OWL vocabulary.
     */
    private static boolean isInstanceFact(Statement st, Set<String> aboxSubjects) {
        String subject = st.getSubject().getURI();
        if (subject == null || !aboxSubjects.contains(subject)) {
            return false;
        }
        if (!st.getPredicate().getURI().equals(RDF_TYPE)) {
            return true;
        }
        RDFNode object = st.getObject();
        if (!object.isURIResource()) {
            return true;
        }
        String iri = object.asResource().getURI();
        return !(iri.startsWith(RDF_NS) || iri.startsWith(RDFS_NS)
                || iri.startsWith(XSD_NS) || iri.startsWith(OWL_NS));
    }

    private static final String RDF_TYPE =
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";
    private static final String RDF_NS =
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
    private static final String RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#";
    private static final String XSD_NS = "http://www.w3.org/2001/XMLSchema#";
    private static final String OWL_NS = "http://www.w3.org/2002/07/owl#";

    private static List<Rule> loadRules(Path rulesDir) throws Exception {
        List<Rule> rules = new ArrayList<>();
        if (!Files.isDirectory(rulesDir)) {
            throw new IllegalArgumentException("rules directory not found: " + rulesDir);
        }
        List<Path> files = new ArrayList<>();
        try (Stream<Path> s = Files.list(rulesDir)) {
            s.filter(p -> p.getFileName().toString().endsWith(".rules"))
             .sorted()
             .forEach(files::add);
        }
        for (Path file : files) {
            try (BufferedReader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
                rules.addAll(Rule.parseRules(Rule.rulesParserFromReader(r)));
            }
        }
        return rules;
    }

    private RulesRunner() {}
}
