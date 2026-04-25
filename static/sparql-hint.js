/**
 * SPARQL auto-complete hint for CodeMirror 5.
 * Covers: SPARQL keywords, well-known prefixes, prefix:localname terms,
 * and prefixes declared in the current query.
 */

const SPARQL_KEYWORDS = [
  'SELECT', 'SELECT DISTINCT', 'SELECT REDUCED',
  'ASK', 'CONSTRUCT', 'DESCRIBE',
  'WHERE', 'FROM', 'FROM NAMED',
  'PREFIX', 'BASE',
  'OPTIONAL', 'FILTER', 'BIND', 'VALUES', 'MINUS',
  'UNION', 'GRAPH', 'SERVICE', 'SILENT',
  'LIMIT', 'OFFSET', 'ORDER BY', 'GROUP BY', 'HAVING',
  'ASC', 'DESC', 'DISTINCT', 'REDUCED',
  'INSERT', 'DELETE', 'INSERT DATA', 'DELETE DATA',
  'LOAD', 'CLEAR', 'DROP', 'CREATE', 'COPY', 'MOVE', 'ADD',
  'WITH', 'USING', 'USING NAMED',
  'EXISTS', 'NOT EXISTS', 'IN', 'NOT IN',
  // Functions
  'STR', 'LANG', 'LANGMATCHES', 'DATATYPE', 'BOUND',
  'IRI', 'URI', 'BNODE', 'RAND', 'ABS', 'CEIL', 'FLOOR', 'ROUND',
  'CONCAT', 'STRLEN', 'SUBSTR', 'UCASE', 'LCASE',
  'ENCODE_FOR_URI', 'CONTAINS', 'STRSTARTS', 'STRENDS',
  'STRBEFORE', 'STRAFTER', 'REPLACE', 'REGEX',
  'YEAR', 'MONTH', 'DAY', 'HOURS', 'MINUTES', 'SECONDS',
  'TIMEZONE', 'TZ', 'NOW', 'UUID', 'STRUUID',
  'MD5', 'SHA1', 'SHA256', 'SHA384', 'SHA512',
  'COALESCE', 'IF', 'STRLANG', 'STRDT', 'sameTerm',
  'isIRI', 'isURI', 'isBLANK', 'isLITERAL', 'isNUMERIC',
  'COUNT', 'SUM', 'MIN', 'MAX', 'AVG', 'SAMPLE', 'GROUP_CONCAT',
  'SEPARATOR', 'true', 'false', 'a',
];

const WELL_KNOWN_PREFIXES = {
  rdf:      'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs:     'http://www.w3.org/2000/01/rdf-schema#',
  owl:      'http://www.w3.org/2002/07/owl#',
  xsd:      'http://www.w3.org/2001/XMLSchema#',
  skos:     'http://www.w3.org/2004/02/skos/core#',
  skosxl:   'http://www.w3.org/2008/05/skos-xl#',
  dcterms:  'http://purl.org/dc/terms/',
  dc:       'http://purl.org/dc/elements/1.1/',
  foaf:     'http://xmlns.com/foaf/0.1/',
  schema:   'https://schema.org/',
  prov:     'http://www.w3.org/ns/prov#',
  void:     'http://rdfs.org/ns/void#',
  dcat:     'http://www.w3.org/ns/dcat#',
  sh:       'http://www.w3.org/ns/shacl#',
  shex:     'http://www.w3.org/ns/shex#',
  geo:      'http://www.opengis.net/ont/geosparql#',
  wgs:      'http://www.w3.org/2003/01/geo/wgs84_pos#',
  obo:      'http://purl.obolibrary.org/obo/',
  oboInOwl: 'http://www.geneontology.org/formats/oboInOwl#',
  iao:      'http://purl.obolibrary.org/obo/IAO_',
  ro:       'http://purl.obolibrary.org/obo/RO_',
  bfo:      'http://purl.obolibrary.org/obo/BFO_',
  ex:       'http://example.org/',
  wd:       'http://www.wikidata.org/entity/',
  wdt:      'http://www.wikidata.org/prop/direct/',
  wikibase: 'http://wikiba.se/ontology#',
  bd:       'http://www.bigdata.com/rdf#',
  p:        'http://www.wikidata.org/prop/',
  ps:       'http://www.wikidata.org/prop/statement/',
  pq:       'http://www.wikidata.org/prop/qualifier/',
  pr:       'http://www.wikidata.org/prop/reference/',
};

// Common local names per well-known prefix
const PREFIX_TERMS = {
  rdf:     ['type', 'Property', 'Statement', 'subject', 'predicate', 'object',
             'value', 'List', 'nil', 'first', 'rest', 'langString', 'HTML', 'XMLLiteral'],
  rdfs:    ['label', 'comment', 'subClassOf', 'subPropertyOf', 'domain', 'range',
             'Class', 'Literal', 'Datatype', 'Resource', 'Container', 'isDefinedBy',
             'seeAlso', 'member'],
  owl:     ['Class', 'ObjectProperty', 'DatatypeProperty', 'AnnotationProperty',
             'Individual', 'Thing', 'Nothing', 'NamedIndividual', 'Ontology',
             'equivalentClass', 'equivalentProperty', 'sameAs', 'differentFrom',
             'disjointWith', 'inverseOf', 'imports', 'deprecated', 'versionInfo',
             'unionOf', 'intersectionOf', 'complementOf', 'oneOf',
             'someValuesFrom', 'allValuesFrom', 'hasValue',
             'minCardinality', 'maxCardinality', 'cardinality',
             'Restriction', 'FunctionalProperty', 'InverseFunctionalProperty',
             'TransitiveProperty', 'SymmetricProperty'],
  xsd:     ['string', 'boolean', 'integer', 'decimal', 'float', 'double',
             'date', 'dateTime', 'time', 'duration', 'anyURI',
             'int', 'long', 'short', 'byte',
             'nonNegativeInteger', 'positiveInteger', 'negativeInteger',
             'normalizedString', 'token', 'language', 'gYear', 'gMonth', 'gDay'],
  skos:    ['Concept', 'ConceptScheme', 'Collection', 'OrderedCollection',
             'inScheme', 'hasTopConcept', 'topConceptOf',
             'prefLabel', 'altLabel', 'hiddenLabel',
             'broader', 'narrower', 'related', 'broaderTransitive', 'narrowerTransitive',
             'definition', 'note', 'scopeNote', 'example', 'historyNote',
             'editorialNote', 'changeNote', 'notation',
             'mappingRelation', 'closeMatch', 'exactMatch', 'broadMatch', 'narrowMatch', 'relatedMatch'],
  dcterms: ['title', 'description', 'creator', 'contributor', 'publisher',
             'date', 'created', 'modified', 'issued', 'subject', 'type',
             'format', 'identifier', 'source', 'language', 'relation',
             'rights', 'license', 'accessRights', 'coverage', 'temporal',
             'spatial', 'isPartOf', 'hasPart', 'conformsTo', 'provenance',
             'Agent', 'MediaType', 'LicenseDocument'],
  foaf:    ['Person', 'Organization', 'Agent', 'Document', 'Image', 'Group',
             'name', 'firstName', 'familyName', 'nick', 'title',
             'mbox', 'homepage', 'depiction', 'knows', 'member', 'primaryTopic',
             'based_near', 'account', 'OnlineAccount'],
  schema:  ['Person', 'Organization', 'CreativeWork', 'Article', 'Dataset',
             'Event', 'Place', 'Product', 'Service',
             'name', 'description', 'url', 'identifier', 'sameAs',
             'author', 'creator', 'publisher', 'dateCreated', 'dateModified',
             'license', 'keywords', 'about', 'image', 'citation',
             'SoftwareSourceCode', 'SoftwareApplication', 'WebAPI',
             'target', 'query'],
  prov:    ['Entity', 'Activity', 'Agent', 'wasGeneratedBy', 'used',
             'wasAssociatedWith', 'wasAttributedTo', 'wasDerivedFrom',
             'wasInformedBy', 'startedAtTime', 'endedAtTime', 'atTime'],
  dcat:    ['Dataset', 'Distribution', 'Catalog', 'DataService', 'Resource',
             'theme', 'keyword', 'distribution', 'downloadURL', 'accessURL',
             'mediaType', 'byteSize', 'landingPage', 'contactPoint',
             'temporal', 'spatial', 'publisher', 'accessService'],
  sh:      ['NodeShape', 'PropertyShape', 'property', 'path', 'targetClass',
             'targetNode', 'targetSubjectsOf', 'targetObjectsOf',
             'minCount', 'maxCount', 'datatype', 'nodeKind',
             'class', 'node', 'IRI', 'Literal', 'BlankNode',
             'minLength', 'maxLength', 'pattern', 'flags',
             'minExclusive', 'maxExclusive', 'minInclusive', 'maxInclusive',
             'in', 'or', 'and', 'not', 'xone',
             'severity', 'message', 'name', 'description',
             'Violation', 'Warning', 'Info', 'ValidationReport', 'ValidationResult'],
  obo:     ['IAO_0000115', 'IAO_0000118', 'IAO_0000111'],
  geo:     ['Feature', 'Geometry', 'hasGeometry', 'asWKT', 'asGML',
             'sfWithin', 'sfContains', 'sfOverlaps', 'sfIntersects',
             'wktLiteral', 'gmlLiteral'],
  void:    ['Dataset', 'Linkset', 'sparqlEndpoint', 'triples', 'entities',
             'classes', 'properties', 'distinctSubjects', 'distinctObjects',
             'subset', 'vocabulary', 'exampleResource'],
};

function parseDeclaredPrefixes(queryText) {
  const declared = {};
  const re = /PREFIX\s+(\w*):\s*<([^>]+)>/gi;
  let m;
  while ((m = re.exec(queryText)) !== null) {
    declared[m[1]] = m[2];
  }
  return declared;
}

function sparqlHint(cm) {
  const cursor = cm.getCursor();
  const line   = cm.getLine(cursor.line);
  const pos    = cursor.ch;

  // Find token start — allow word chars, colon, hyphen
  let start = pos;
  while (start > 0 && /[\w:-]/.test(line[start - 1])) start--;
  const word = line.slice(start, pos);

  const queryText = cm.getValue();
  const declared  = parseDeclaredPrefixes(queryText);
  const allPrefixes = Object.assign({}, WELL_KNOWN_PREFIXES, declared);

  let list = [];

  // Context 1: right after "PREFIX " → suggest full prefix declarations
  const beforeCursor = line.slice(0, pos);
  if (/\bPREFIX\s+[\w]*$/i.test(beforeCursor)) {
    const typed = word; // what the user typed so far (prefix name fragment)
    for (const [pfx, uri] of Object.entries(WELL_KNOWN_PREFIXES)) {
      if (!typed || pfx.startsWith(typed)) {
        list.push({
          text:        `${pfx}: <${uri}>`,
          displayText: `${pfx}: <${uri}>`,
        });
      }
    }
    list.sort((a, b) => a.text.localeCompare(b.text));
    return { list, from: CodeMirror.Pos(cursor.line, start), to: CodeMirror.Pos(cursor.line, pos) };
  }

  // Context 2: "prefix:localname" — suggest local names for that prefix
  const colonIdx = word.indexOf(':');
  if (colonIdx !== -1) {
    const pfx       = word.slice(0, colonIdx);
    const local     = word.slice(colonIdx + 1);
    const terms     = PREFIX_TERMS[pfx] || [];
    for (const term of terms) {
      if (!local || term.startsWith(local)) {
        list.push({ text: `${pfx}:${term}`, displayText: `${pfx}:${term}` });
      }
    }
    // If we have declared prefixes with no known terms, still complete the prefix: part
    if (!list.length && allPrefixes[pfx]) {
      list.push({ text: `${pfx}:`, displayText: `${pfx}: <${allPrefixes[pfx]}>` });
    }
    return { list, from: CodeMirror.Pos(cursor.line, start), to: CodeMirror.Pos(cursor.line, pos) };
  }

  // Context 3: general — keywords + prefix names
  if (word.length >= 1) {
    const lc = word.toLowerCase();

    // Keywords
    for (const kw of SPARQL_KEYWORDS) {
      if (kw.toLowerCase().startsWith(lc)) {
        list.push(kw);
      }
    }

    // Prefix names (offer "prefix:" so user can continue typing local name)
    for (const [pfx, uri] of Object.entries(allPrefixes)) {
      if (pfx.startsWith(word) || pfx.toLowerCase().startsWith(lc)) {
        list.push({ text: `${pfx}:`, displayText: `${pfx}: <${uri}>` });
      }
    }
  }

  // Deduplicate
  const seen = new Set();
  list = list.filter(item => {
    const key = typeof item === 'string' ? item : item.text;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return { list, from: CodeMirror.Pos(cursor.line, start), to: CodeMirror.Pos(cursor.line, pos) };
}
