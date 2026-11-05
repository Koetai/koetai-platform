// The trail as URL parameters. A trail is a list of steps:
//   c=<iri>            card for an entity
//   l=<dir> <p> [<t>]  list of entities linked to the preceding card
//   t=<type>           list of instances of a type
//   val=[p, term]      list of entities sharing a date value
//   q=<text>           exact-label search
//   view=map           show the preceding list as a map

export const TRAIL_KEYS = ['c', 'l', 't', 'val', 'q', 'view'];

// [[key, value], ...] (e.g. from URLSearchParams) -> steps. Other keys are ignored.
export function trailFromEntries(entries) {
  const trail = [];
  for (const [k, v] of entries) {
    if (k === 'c') trail.push({ k: 'card', iri: v });
    else if (k === 'l') {
      const [dir, p, t = ''] = v.split(' ');
      trail.push({ k: 'links', dir, p, t });
    } else if (k === 't') trail.push({ k: 'type', t: v });
    else if (k === 'val') {
      try {
        const [p, o] = JSON.parse(v);
        trail.push({ k: 'value', p, o });
      } catch { /* ignore malformed step */ }
    } else if (k === 'q') trail.push({ k: 'search', q: v });
    else if (k === 'view' && trail.length) trail[trail.length - 1].view = v;
  }
  return trail;
}

// steps -> [[key, value], ...]
export function trailToEntries(trail) {
  const out = [];
  for (const s of trail) {
    if (s.k === 'card') out.push(['c', s.iri]);
    else if (s.k === 'links') out.push(['l', [s.dir, s.p, s.t].filter(Boolean).join(' ')]);
    else if (s.k === 'type') out.push(['t', s.t]);
    else if (s.k === 'value') out.push(['val', JSON.stringify([s.p, s.o])]);
    else if (s.k === 'search') out.push(['q', s.q]);
    if (s.view) out.push(['view', s.view]);
  }
  return out;
}

export const trailToString = (trail) => new URLSearchParams(trailToEntries(trail)).toString();
export const trailFromString = (s) => trailFromEntries(new URLSearchParams(s || ''));
