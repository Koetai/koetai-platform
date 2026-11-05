// SPARQL client: POST form-encoded (a CORS "simple" request, so no preflight),
// with a small concurrency limit, a timeout, and an in-memory result cache.

export class SparqlClient {
  constructor(url, { timeoutMs = 25000, maxConcurrent = 3 } = {}) {
    this.url = url;
    this.timeoutMs = timeoutMs;
    this.maxConcurrent = maxConcurrent;
    this.active = 0;
    this.waiting = [];
    this.cache = new Map();
  }

  // Returns an array of rows; each row maps variable name to an RDF term
  // { type: 'uri' | 'literal' | 'bnode', value, datatype?, 'xml:lang'? }.
  select(query) {
    if (!this.cache.has(query)) {
      const p = this.#enqueue(() => this.#fetch(query));
      p.catch(() => this.cache.delete(query)); // do not cache failures
      this.cache.set(query, p);
    }
    return this.cache.get(query);
  }

  #enqueue(task) {
    return new Promise((resolve, reject) => {
      this.waiting.push({ task, resolve, reject });
      this.#pump();
    });
  }

  #pump() {
    while (this.active < this.maxConcurrent && this.waiting.length) {
      const { task, resolve, reject } = this.waiting.shift();
      this.active++;
      task().then(resolve, reject).finally(() => {
        this.active--;
        this.#pump();
      });
    }
  }

  async #fetch(query) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    try {
      const res = await fetch(this.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          Accept: 'application/sparql-results+json',
        },
        body: new URLSearchParams({ query }),
        signal: ctrl.signal,
      });
      if (!res.ok) throw new Error(`Endpoint returned HTTP ${res.status}`);
      const json = await res.json();
      return json.results.bindings;
    } catch (err) {
      if (err.name === 'AbortError') throw new Error(`Query timed out after ${this.timeoutMs / 1000}s`);
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }
}
