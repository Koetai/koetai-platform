// Map view of a set, drawn with Leaflet. Leaflet is loaded the first time a
// map is shown (from cdnjs unless the host says otherwise, or not at all if
// the page already has window.L), so the rest of the browser has no
// dependencies.

const LEAFLET = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4';
let leafletP = null;

// Default tiles: OpenStreetMap's public tile server. Fine for light use; a
// busy site should pass its own provider (see mount()'s `tiles` option).
const OSM_TILES = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
};

// `base` is a URL prefix serving leaflet.min.js and leaflet.min.css.
function loadLeaflet(base = LEAFLET) {
  if (window.L) return Promise.resolve(window.L);
  leafletP ??= new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = `${base}/leaflet.min.css`;
    document.head.append(css);
    const js = document.createElement('script');
    js.src = `${base}/leaflet.min.js`;
    js.onload = () => resolve(window.L);
    js.onerror = () => { leafletP = null; reject(new Error('Could not load Leaflet')); };
    document.head.append(js);
  });
  return leafletP;
}

// Draw `points` ([{ x, lat, long }]) in `el`. `onPick(x)` is called when a
// point is clicked; `labelFor(x)` resolves to a tooltip label; `leaflet` is
// an optional URL prefix for Leaflet; `tiles` is { url, attribution,
// maxZoom, ... } (anything else is passed to L.tileLayer as an option).
// Returns { highlight(x), resize() }.
export async function drawMap(el, points, { onPick, labelFor, leaflet, tiles }) {
  const L = await loadLeaflet(leaflet);
  const accent = getComputedStyle(el).getPropertyValue('--accent').trim() || '#6a5acd';
  const map = L.map(el, { preferCanvas: true, worldCopyJump: true });
  const { url, ...tileOptions } = { ...OSM_TILES, ...tiles };
  L.tileLayer(url, tileOptions).addTo(map);

  const plain = { radius: 5, weight: 1, color: '#fff', fillColor: accent, fillOpacity: 0.8 };
  const picked = { radius: 8, weight: 2, color: '#1d2330', fillColor: '#ffb400', fillOpacity: 1 };
  const markers = new Map();
  for (const pt of points) {
    const m = L.circleMarker([pt.lat, pt.long], plain).addTo(map);
    m.bindTooltip('…');
    m.on('tooltipopen', async () => {
      if (m.labelled) return;
      m.labelled = true;
      try { m.setTooltipContent(await labelFor(pt.x)); } catch { m.setTooltipContent(pt.x); }
    });
    m.on('click', () => onPick(pt.x));
    markers.set(pt.x, m); // several points per entity are rare; keep the last
  }

  if (points.length) {
    map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.long])), { padding: [20, 20], maxZoom: 8 });
  } else {
    map.setView([20, 0], 1);
  }

  let current = null;
  return {
    highlight(x) {
      if (current) current.setStyle(plain);
      current = markers.get(x) || null;
      if (current) current.setStyle(picked).bringToFront();
    },
    resize() { map.invalidateSize(); },
  };
}
