/** B1 rule 3: distance is computed per request, never stored. Same formula
 * as `services/warehouse/app/main.py` `_haversine_km`. */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const phi1 = toRad(lat1);
  const phi2 = toRad(lat2);
  const dphi = toRad(lat2 - lat1);
  const dlam = toRad(lon2 - lon1);
  const a =
    Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlam / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}
