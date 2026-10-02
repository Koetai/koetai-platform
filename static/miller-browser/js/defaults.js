// Built-in settings. An endpoint (or the options passed to mount()) can
// override any of these.

export const DEFAULTS = {
  // Candidate label properties, in order of preference. An entry in <...>/<...>
  // form is a property path. A lens can add more in front of these.
  labelProperties: [
    'http://www.w3.org/2004/02/skos/core#prefLabel',
    // SKOS-XL: the label is a node of its own, e.g. the Crossref Funder Registry.
    '<http://www.w3.org/2008/05/skos-xl#prefLabel>/<http://www.w3.org/2008/05/skos-xl#literalForm>',
    'http://www.w3.org/2000/01/rdf-schema#label',
    'https://schema.org/name',
    'http://schema.org/name',
    'http://purl.org/dc/terms/title',
    'http://purl.org/dc/elements/1.1/title',
    'http://xmlns.com/foaf/0.1/name',
    // Last resort, so that SKOS-XL label nodes (e.g. a list of altLabels) read
    // as their text.
    'http://www.w3.org/2008/05/skos-xl#literalForm',
  ],

  // Plumbing hidden from cards and link groups. A lens can add more.
  // skosxl:prefLabel is the entity's title already (via the path above).
  hiddenPredicates: ['http://www.w3.org/2008/05/skos-xl#prefLabel'],
  hiddenTypes: [],

  // Types never offered as search results (on top of hidden types): SKOS-XL
  // label nodes carry the matched text but the entity they label is the hit.
  notSearchable: ['http://www.w3.org/2008/05/skos-xl#Label'],

  // Every query is bounded: links are discovered from a sample, counts are
  // capped, lists are paged and only sorted when small.
  sampleLimit: 2000,
  countCap: 10000,
  sortLimit: 5000,
  pageSize: 50,
  timeoutMs: 25000,
  maxConcurrent: 3,

  // Types whose instances are pictures. The picture's URL is the first of
  // `properties` that has a value, else the entity's own IRI. A lens `image`
  // template for the type overrides this (or turns it off with false).
  imageTypes: {
    'http://xmlns.com/foaf/0.1/Image': {},
    'https://schema.org/ImageObject': { properties: ['https://schema.org/contentUrl', 'https://schema.org/url'] },
    'http://schema.org/ImageObject': { properties: ['http://schema.org/contentUrl', 'http://schema.org/url'] },
  },

  // How an entity can carry a point, for the map view: WKT literals, or
  // latitude/longitude pairs.
  geo: {
    wkt: [
      'http://www.wikidata.org/prop/direct/P625',
      'http://www.opengis.net/ont/geosparql#asWKT',
    ],
    latLong: [
      ['http://rs.tdwg.org/dwc/terms/decimalLatitude', 'http://rs.tdwg.org/dwc/terms/decimalLongitude'],
      ['http://www.w3.org/2003/01/geo/wgs84_pos#lat', 'http://www.w3.org/2003/01/geo/wgs84_pos#long'],
      ['https://schema.org/latitude', 'https://schema.org/longitude'],
    ],
  },
  mapLimit: 1000,
};
