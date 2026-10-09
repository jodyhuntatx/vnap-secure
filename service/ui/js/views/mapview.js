// Leaflet map helpers (Leaflet is loaded as the global L from vendor/leaflet).
import { get } from "../api.js";

let uiConfig = null;

export async function getUiConfig() {
  if (!uiConfig) {
    try { uiConfig = await get("/ui-config"); } catch (e) { uiConfig = {}; }
  }
  return uiConfig;
}

export async function makeMap(el, center, zoom) {
  await getUiConfig();
  const map = L.map(el, { zoomControl: true }).setView(center || uiConfig.origin || [40.208106, -8.4197756], zoom || 16);
  if (uiConfig.tile_url) {
    // Tile servers such as OpenStreetMap's refuse requests without a Referer; the page's policy is
    // no-referrer, so tiles alone send the site's origin (never a path).
    L.tileLayer(uiConfig.tile_url, { maxZoom: 19, attribution: uiConfig.tile_attribution || "", referrerPolicy: "strict-origin" }).addTo(map);
  }
  return map;
}

// metres to degrees around a latitude (as the mobility client does)
export function offset(center, northM, eastM) {
  return [center[0] + northM / 111195.0, center[1] + eastM / (111195.0 * Math.cos(center[0] * Math.PI / 180))];
}

// a crossing: four arms of arm_m metres ending on a square ring road
export function crossingLayer(center, arm, color) {
  const g = L.layerGroup();
  const ends = [[arm, 0], [0, arm], [-arm, 0], [0, -arm]].map(([n, e]) => offset(center, n, e));
  for (const end of ends) L.polyline([center, end], { color, weight: 3 }).addTo(g);
  const ring = [[arm, arm], [arm, -arm], [-arm, -arm], [-arm, arm], [arm, arm]].map(([n, e]) => offset(center, n, e));
  L.polyline(ring, { color, weight: 2, dashArray: "4 6" }).addTo(g);
  return g;
}

export function stationColor(stationType) {
  const css = getComputedStyle(document.documentElement);
  return (stationType === 15 ? css.getPropertyValue("--rsu") : css.getPropertyValue("--car")).trim() || "#1f5fae";
}

export function zoneColor() {
  return getComputedStyle(document.documentElement).getPropertyValue("--zone").trim() || "#7a3fb0";
}
