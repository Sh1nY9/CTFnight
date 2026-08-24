const FORMULA_PREFIX = /^(?:[\t\r\n]|[\u0000-\u0020]*[=+\-@])/;

/**
 * Prefixes spreadsheet formula triggers with an apostrophe, then applies RFC 4180 quoting.
 * All exported audit values are treated as untrusted, including names and challenge titles.
 */
export function escapeCsvCell(value: unknown): string {
  const raw = String(value ?? "");
  const neutralized = FORMULA_PREFIX.test(raw) ? `'${raw}` : raw;
  return `"${neutralized.replaceAll('"', '""')}"`;
}

export function serializeCsv(rows: readonly (readonly unknown[])[]): string {
  return rows.map((row) => row.map(escapeCsvCell).join(",")).join("\r\n");
}
