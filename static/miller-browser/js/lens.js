// Lenses: per-endpoint JSON overrides (titles, summaries, sort orders, edge
// labels, hidden plumbing). Everything here is optional; with
// an empty lens the browser runs in generic mode.
//
// Wherever a lens names a property it may instead give a SPARQL property
// path, e.g. "skosxl:prefLabel/skosxl:literalForm". Internally a "ref" is
// either a full IRI (a plain property) or an expanded path containing "<".

export const RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';

export class Lens {
  constructor(json = {}, defaults = {}) {
    this.prefixes = json.prefixes || {};
    const x = (c) => this.expand(c);
    const r = (c) => this.ref(c);

    // Lens label properties come first, then the endpoint defaults.
    this.labelProperties = [...new Set([...(json.labelProperties || []).map(r), ...(defaults.labelProperties || [])])];
    this.imageTypes = new Map(Object.entries(defaults.imageTypes || {}));
    this.predicateLabels = new Map(Object.entries(json.predicateLabels || {}).map(([p, l]) => [x(p), l]));
    this.hiddenTypes = new Set([...(json.hiddenTypes || []).map(x), ...(defaults.hiddenTypes || [])]);
    this.hiddenPredicates = new Set([...(json.hiddenPredicates || []).map(x), ...(defaults.hiddenPredicates || [])]);

    this.types = new Map();
    for (const [t, spec] of Object.entries(json.types || {})) {
      this.types.set(x(t), {
        title: this.#titleTemplates(spec.title),
        summary: (spec.summary || []).map(r),
        fields: (spec.fields || []).map((f) => ({ label: f.label, ref: r(f.path) })),
        sort: spec.sort ? { property: x(spec.sort.property), numeric: !!spec.sort.numeric } : null,
        image: spec.image ?? null, // a template, or false for no picture
      });
    }

    this.edges = new Map();
    for (const [key, spec] of Object.entries(json.edges || {})) {
      const [dir, p, t] = key.split(/\s+/);
      this.edges.set(edgeKey(dir, x(p), t ? x(t) : ''), spec);
    }
  }

  // A lens title is either a list of properties to join with spaces, e.g.
  // ["schema:givenName", "schema:familyName"], or a list of templates tried in
  // order, e.g. ["{bhlv:pagePrefix} {bhlv:pageNumber}", "Seq. {bhlv:sequenceOrder}"].
  // Returns [{ text, props }] with placeholders expanded to full IRIs.
  #titleTemplates(title) {
    if (!title) return [];
    const list = Array.isArray(title) ? title : [title];
    const templates = list.some((s) => s.includes('{')) ? list : [list.map((p) => `{${p}}`).join(' ')];
    return templates.map((text) => {
      const props = [];
      const expanded = text.replace(/\{([^}]+)\}/g, (_, c) => {
        props.push(this.ref(c));
        return `{${props.length - 1}}`;
      });
      return { text: expanded, props };
    });
  }

  // "schema:name" -> "https://schema.org/name"; full IRIs pass through.
  expand(curie) {
    if (!curie || /^https?:\/\//.test(curie)) return curie;
    const i = curie.indexOf(':');
    if (i < 0) return curie;
    const ns = this.prefixes[curie.slice(0, i)];
    return ns ? ns + curie.slice(i + 1) : curie;
  }

  ref(s) {
    if (/^https?:\/\//.test(s) || !/[/^|]/.test(s)) return this.expand(s);
    return this.expandPath(s);
  }

  // Expand CURIEs inside a SPARQL property path into <IRI>s.
  expandPath(path) {
    return path.replace(/([A-Za-z][\w-]*):([\w.-]+)/g, (m) => `<${this.expand(m)}>`);
  }

  // The lens spec for the first of `types` that has one.
  forTypes(types = []) {
    for (const t of types) if (this.types.has(t)) return this.types.get(t);
    return null;
  }

  edgeLabel(dir, p, t) {
    return (this.edges.get(edgeKey(dir, p, t || '')) || this.edges.get(edgeKey(dir, p, '')))?.label;
  }

  // The picture for an entity, or null: from the lens `image` template for its
  // type if there is one, else from the default image types (the first of
  // their properties with a value in `values`, else the IRI itself).
  imageFor(iri, types, values) {
    const spec = this.forTypes(types);
    if (spec?.image === false) return null;
    if (spec?.image) return spec.image.replace('{local}', localName(iri)).replace('{iri}', iri);
    for (const t of types) {
      const d = this.imageTypes.get(t);
      if (!d) continue;
      for (const p of d.properties || []) {
        const v = values?.get(p)?.[0];
        if (v) return v;
      }
      return iri;
    }
    return null;
  }

  // Is the entity itself a picture (rather than something with one)?
  isImage(types) {
    return types.some((t) => this.imageTypes.has(t));
  }

  // All refs worth fetching to label and summarise a list item, split into
  // plain properties and paths (which need a query of their own).
  get briefRefs() {
    const s = new Set([RDF_TYPE, ...this.labelProperties]);
    for (const d of this.imageTypes.values()) (d.properties || []).forEach((p) => s.add(p));
    for (const spec of this.types.values()) {
      spec.title.forEach((tpl) => tpl.props.forEach((p) => s.add(p)));
      spec.summary.forEach((p) => s.add(p));
    }
    const all = [...s];
    return { props: all.filter((p) => !isPath(p)), paths: all.filter(isPath) };
  }
}

const edgeKey = (dir, p, t) => `${dir} ${p} ${t}`;

export const isPath = (ref) => ref.includes('<');

export function localName(iri) {
  if (!iri) return '';
  const m = iri.match(/[#/:]([^#/:]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : iri;
}

// A readable label for an entity from its property values:
// the first lens title template with any value filled, else the first label
// property, else the IRI tail.
export function labelFrom(iri, values, lens) {
  const types = values.get(RDF_TYPE) || [];
  const spec = lens.forTypes(types);
  for (const tpl of spec?.title || []) {
    let filled = false;
    const text = tpl.text.replace(/\{(\d+)\}/g, (_, i) => {
      const v = values.get(tpl.props[i])?.[0];
      if (v) filled = true;
      return v || '';
    });
    if (filled) return text.replace(/\s+/g, ' ').trim();
  }
  for (const p of lens.labelProperties) {
    const v = values.get(p);
    if (v?.length) return v[0];
  }
  return localName(iri);
}

export function imageFor(iri, types, lens, values) {
  return lens.imageFor(iri, types, values);
}
