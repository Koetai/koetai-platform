// Query builders and result shaping. Every query is bounded: sampled
// subqueries for discovery, LIMIT+1 capped counts, paged lists, and sorting
// only for sets small enough to sort cheaply.

import { RDF_TYPE, labelFrom, localName, isPath } from './lens.js';

export const iri = (s) => `<${s}>`;

export function termToSparql(t) {
  if (t.type === 'uri') return iri(t.value);
  const v = JSON.stringify(t.value);
  if (t['xml:lang']) return `${v}@${t['xml:lang']}`;
  if (t.datatype) return `${v}^^<${t.datatype}>`;
  return v;
}

const values = (v, list) => `VALUES ?${v} { ${list.map(iri).join(' ')} }`;

// [lat, long] from a WKT point, e.g. "POINT(-83.08 40.17)", optionally with
// a leading CRS IRI. Other geometries are not mapped (yet).
function parseWktPoint(wkt) {
  const m = wkt.match(/POINT\s*\(\s*([-+\d.eE]+)\s+([-+\d.eE]+)/i);
  return m ? [Number(m[2]), Number(m[1])] : null;
}

// `pattern` (using ?x) evaluated once per subject with ?x bound as a constant,
// as a UNION. `VALUES ?x { ... } ?x ?p ?o` looks equivalent, but Oxigraph
// plans it badly and QLever scans the whole index (60s+ on full BHL); a bound
// subject is an index lookup on every engine.
const perSubject = (xs, pattern) =>
  xs.map((x) => `{ ${pattern.replaceAll('?x', iri(x))} BIND(${iri(x)} AS ?x) }`).join(' UNION ');

export class Queries {
  constructor(client, lens, cfg) {
    this.client = client;
    this.lens = lens;
    this.cfg = cfg;
    this.predLabels = new Map();
    this.predPending = new Map();
  }

  get plainLabelProps() {
    return this.lens.labelProperties.filter((p) => !isPath(p));
  }

  get labelPaths() {
    return this.lens.labelProperties.filter(isPath);
  }

  // Values of lens property paths for a batch of entities:
  // Map x -> Map path -> [term]. One query, one UNION branch per path.
  async pathValues(xs, paths) {
    const out = new Map(xs.map((x) => [x, new Map()]));
    if (!xs.length || !paths.length) return out;
    const branches = paths.map((path, i) => `{ ?x ${path} ?o BIND(${i} AS ?k) }`).join(' UNION ');
    const rows = await this.client.select(
      `SELECT ?x ?k ?o WHERE { ${perSubject(xs, `{ ${branches} }`)} } LIMIT ${xs.length * paths.length * 20}`);
    for (const { x, k, o } of rows) {
      const m = out.get(x.value);
      const path = paths[Number(k.value)];
      if (!m.has(path)) m.set(path, []);
      m.get(path).push(o);
    }
    return out;
  }

  // Readable names for predicates, from the lens or from the store's own
  // labels. In-flight lookups are shared, so cards built together wait for
  // the same answer rather than seeing placeholders.
  async predicateLabels(ps) {
    const todo = [...new Set(ps)].filter((p) => !this.predPending.has(p));
    if (todo.length) {
      const ask = todo.filter((p) => !this.lens.predicateLabels.has(p));
      const fetched = this.#fetchPredicateLabels(ask);
      for (const p of todo) this.predPending.set(p, fetched);
    }
    await Promise.all(ps.map((p) => this.predPending.get(p)));
    return this.predLabels;
  }

  async #fetchPredicateLabels(ask) {
    if (!ask.length || !this.plainLabelProps.length) return;
    const props = this.plainLabelProps.map(iri).join(', ');
    const rows = await this.client.select(
      `SELECT ?x ?p ?o WHERE { ${perSubject(ask, `?x ?p ?o FILTER(?p IN (${props}) && isLiteral(?o))`)} } LIMIT ${ask.length * 10}`);
    const rank = (p) => this.plainLabelProps.indexOf(p);
    const best = new Map();
    for (const { x, p, o } of rows) {
      const cur = best.get(x.value);
      if (!cur || rank(p.value) < cur.r) best.set(x.value, { r: rank(p.value), v: o.value });
    }
    for (const [x, { v }] of best) this.predLabels.set(x, v);
  }

  predName(p) {
    return this.predLabels.get(p) || this.lens.predicateLabels.get(p) || localName(p);
  }

  // All triples with `s` as subject (bounded), grouped by predicate.
  async card(s) {
    const rows = await this.client.select(
      `SELECT ?p ?o WHERE { ${iri(s)} ?p ?o } LIMIT 500`);
    const props = new Map();
    for (const { p, o } of rows) {
      if (!props.has(p.value)) props.set(p.value, []);
      props.get(p.value).push(o);
    }
    const flat = new Map([...props].map(([p, os]) => [p, os.map((o) => o.value)]));
    const types = flat.get(RDF_TYPE) || [];

    // Lens fields and any label/title/summary paths need their own query.
    const spec = this.lens.forTypes(types);
    const fields = spec?.fields || [];
    const paths = [...new Set([...this.lens.briefRefs.paths, ...fields.map((f) => f.ref)])];
    let pv = new Map();
    try {
      pv = (await this.pathValues([s], paths)).get(s);
    } catch { /* fall back to plain properties */ }
    for (const [path, os] of pv) flat.set(path, os.map((o) => o.value));

    return {
      iri: s,
      props,
      types,
      computed: fields.map((f) => ({ label: f.label, values: pv.get(f.ref) || [] })).filter((f) => f.values.length),
      label: labelFrom(s, flat, this.lens),
      image: this.lens.imageFor(s, types, flat),
      truncated: rows.length >= 500,
    };
  }

  // Link groups by (predicate, target type) in one direction, discovered from
  // a bounded sample of links. If the sample was saturated the counts are
  // lower bounds and the caller should fetch capped counts per group.
  async linkGroups(s, dir) {
    const n = this.cfg.sampleLimit;
    const inner = dir === 'out'
      ? `${iri(s)} ?p ?x FILTER(isIRI(?x) && ?p != <${RDF_TYPE}>)`
      : `?x ?p ${iri(s)} FILTER(isIRI(?x))`;
    const rows = await this.client.select(
      `SELECT ?p ?t (COUNT(?x) AS ?n) WHERE {
  { SELECT ?p ?x WHERE { ${inner} } LIMIT ${n} }
  OPTIONAL { ?x a ?t }
} GROUP BY ?p ?t`);
    const total = rows.reduce((a, r) => a + Number(r.n.value), 0);
    const groups = rows
      .map((r) => ({
        dir,
        p: r.p.value,
        t: r.t?.value || '',
        n: Number(r.n.value),
      }))
      .filter((g) => !this.lens.hiddenPredicates.has(g.p) && !this.lens.hiddenTypes.has(g.t));
    return { groups, saturated: total >= n };
  }

  // Graph pattern binding ?x for the members of a link group from `s`.
  linkPattern(s, dir, p, t) {
    const edge = dir === 'out' ? `${iri(s)} ${iri(p)} ?x .` : `?x ${iri(p)} ${iri(s)} .`;
    const type = t ? `?x a ${iri(t)} .` : 'FILTER NOT EXISTS { ?x a ?anyType }';
    return `${edge} ${type}`;
  }

  typePattern(t) {
    return `?x a ${iri(t)} .`;
  }

  valuePattern(p, o) {
    return `?x ${iri(p)} ${termToSparql(o)} .`;
  }

  searchPattern(text) {
    const lits = [JSON.stringify(text), `${JSON.stringify(text)}@en`].join(' ');
    // The search text goes inside every branch: with one VALUES outside a
    // UNION, Oxigraph evaluates the branches unbound (a 502 on CoL).
    const textValues = `VALUES ?l { ${lits} }`;
    const branches = [`{ ${textValues} ${values('lp', this.plainLabelProps)} ?x ?lp ?l }`,
      ...this.labelPaths.map((path) => `{ ${textValues} ?x ${path} ?l }`)];
    // Hidden plumbing, and label nodes such as SKOS-XL's, are not results.
    const hidden = [...new Set([...this.lens.hiddenTypes, ...(this.cfg.notSearchable || [])])];
    const notHidden = hidden.length ? `FILTER NOT EXISTS { ${values('ht', hidden)} ?x a ?ht }` : '';
    return `${branches.join(' UNION ')} ${notHidden}`;
  }

  // Number of distinct ?x matching `pattern`, capped: returns { n, capped }.
  async count(pattern) {
    const cap = this.cfg.countCap;
    const rows = await this.client.select(
      `SELECT (COUNT(*) AS ?n) WHERE { SELECT DISTINCT ?x WHERE { ${pattern} } LIMIT ${cap + 1} }`);
    const n = Number(rows[0]?.n?.value || 0);
    return n > cap ? { n: cap, capped: true } : { n, capped: false };
  }

  // One page of members. Sorted (by the lens sort key for `t`, else by label)
  // only when the set is known to be small; otherwise in endpoint order.
  async page(pattern, { t, offset = 0, sorted = false }) {
    const size = this.cfg.pageSize;
    let q;
    if (sorted) {
      const sort = t && this.lens.types.get(t)?.sort;
      let keyPattern;
      let key;
      if (sort) {
        keyPattern = `OPTIONAL { ?x ${iri(sort.property)} ?k0 }`;
        key = sort.numeric ? 'MIN(xsd:double(?k0))' : 'MIN(STR(?k0))';
      } else {
        // Sort by label: the plain label properties together, plus one
        // OPTIONAL per label path, in the order the label properties give.
        const parts = [];
        const vars = [];
        for (const p of this.lens.labelProperties) {
          if (isPath(p)) {
            vars.push(`?k${parts.length}`);
            parts.push(`OPTIONAL { ?x ${p} ?k${parts.length} }`);
          } else if (!parts.some((s) => s.includes('?lp'))) {
            vars.push(`?k${parts.length}`);
            parts.push(`OPTIONAL { ${values('lp', this.plainLabelProps)} ?x ?lp ?k${parts.length} }`);
          }
        }
        keyPattern = parts.join(' ');
        key = vars.length > 1 ? `MIN(STR(COALESCE(${vars.join(', ')})))` : `MIN(STR(${vars[0]}))`;
      }
      q = `PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?x (${key} AS ?k) WHERE { ${pattern} ${keyPattern} }
GROUP BY ?x ORDER BY ?k ?x LIMIT ${size} OFFSET ${offset}`;
    } else {
      q = `SELECT DISTINCT ?x WHERE { ${pattern} } LIMIT ${size} OFFSET ${offset}`;
    }
    const rows = await this.client.select(q);
    const xs = rows.map((r) => r.x.value);
    return { items: xs, more: xs.length === size };
  }

  // Label, types and lens summary values for a batch of entities.
  async brief(xs) {
    const out = new Map(xs.map((x) => [x, new Map()]));
    if (!xs.length) return new Map();
    const { props, paths } = this.lens.briefRefs;
    const [rows, pv] = await Promise.all([
      this.client.select(
        `SELECT ?x ?p ?o WHERE { ${perSubject(xs, `?x ?p ?o FILTER(?p IN (${props.map(iri).join(', ')}))`)} } LIMIT ${xs.length * 40}`),
      this.pathValues(xs, paths).catch(() => new Map()),
    ]);
    for (const { x, p, o } of rows) {
      const m = out.get(x.value);
      if (!m.has(p.value)) m.set(p.value, []);
      m.get(p.value).push(o.value);
    }
    for (const [x, m] of pv) {
      for (const [path, os] of m) out.get(x).set(path, os.map((o) => o.value));
    }
    const result = new Map();
    for (const [x, m] of out) {
      const types = m.get(RDF_TYPE) || [];
      const spec = this.lens.forTypes(types);
      const summary = (spec?.summary || []).map((p) => m.get(p)?.[0]).filter(Boolean).join(' · ');
      result.set(x, {
        label: labelFrom(x, m, this.lens),
        types,
        summary,
        // A thumbnail only when the item is itself a picture.
        image: this.lens.isImage(types) ? this.lens.imageFor(x, types, m) : null,
      });
    }
    return result;
  }

  // ---------- geography ----------

  // The ways an entity can carry a point, from config: a WKT literal
  // property, or a latitude/longitude pair. In order of preference.
  get geoKinds() {
    const geo = this.cfg.geo || {};
    return [
      ...(geo.wkt || []).map((p) => ({ wkt: p })),
      ...(geo.latLong || []).map(([lat, long]) => ({ lat, long })),
    ];
  }

  // The first geo kind that any of `xs` has, or null. Bound subjects only,
  // so this is cheap on any engine.
  async geoProbe(xs) {
    const kinds = this.geoKinds;
    if (!xs.length || !kinds.length) return null;
    const branches = kinds
      .map((k, i) => `{ ?x ${iri(k.wkt || k.lat)} ?o BIND(${i} AS ?k) }`)
      .join(' UNION ');
    const rows = await this.client.select(
      `SELECT DISTINCT ?k WHERE { ${perSubject(xs, `{ ${branches} }`)} }`);
    const found = rows.map((r) => Number(r.k.value)).sort((a, b) => a - b);
    return found.length ? kinds[found[0]] : null;
  }

  #geoPattern(kind) {
    return kind.wkt ? `?x ${iri(kind.wkt)} ?a .` : `?x ${iri(kind.lat)} ?a . ?x ${iri(kind.long)} ?b .`;
  }

  // Up to mapLimit members of `pattern` with a point: [{ x, lat, long }].
  async geoPoints(pattern, kind) {
    const rows = await this.client.select(
      `SELECT ?x ?a ?b WHERE { ${pattern} ${this.#geoPattern(kind)} } LIMIT ${this.cfg.mapLimit || 1000}`);
    const points = [];
    for (const r of rows) {
      const ll = kind.wkt ? parseWktPoint(r.a.value) : [Number(r.a.value), Number(r.b.value)];
      if (ll && ll.every(Number.isFinite) && Math.abs(ll[0]) <= 90 && Math.abs(ll[1]) <= 180) {
        points.push({ x: r.x.value, lat: ll[0], long: ll[1] });
      }
    }
    return points;
  }

  // Capped count of members of `pattern` that have a point.
  geoCount(pattern, kind) {
    return this.count(`${pattern} ${this.#geoPattern(kind)}`);
  }

  // Classes in use (bounded), for the start column.
  async types() {
    const rows = await this.client.select('SELECT DISTINCT ?t WHERE { ?s a ?t } LIMIT 100');
    return rows.map((r) => r.t.value).filter((t) => !this.lens.hiddenTypes.has(t));
  }
}
