// Leaflet map helpers (Leaflet is loaded as the global L from vendor/leaflet).
import { get } from "../api.js";

let uiConfig = null;

export async function makeMap(el, center, zoom) {
  if (!uiConfig) {
    try { uiConfig = await get("/ui-config"); } catch (e) { uiConfig = {}; }
  }
  const map = L.map(el, { zoomControl: true }).setView(center || [40.0, -8.0], zoom || 16);
  if (uiConfig.tile_url) {
    L.tileLayer(uiConfig.tile_url, { maxZoom: 19, attribution: uiConfig.tile_attribution || "" }).addTo(map);
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
