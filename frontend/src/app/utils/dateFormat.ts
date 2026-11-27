/** Format ISO YYYY-MM-DD date strings to Vietnamese DD/MM/YYYY display format. */
export function fmtDate(iso: string): string {
  if (!iso || !iso.includes("-")) return iso;
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}
