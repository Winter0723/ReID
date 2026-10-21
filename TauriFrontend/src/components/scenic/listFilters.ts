/** Date fields are local calendar dates; the API accepts a half-open UTC interval. */
export function localDate(iso: string, exclusiveEnd = false): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "";
  if (exclusiveEnd) date.setDate(date.getDate() - 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function dateBounds(start: string, end: string): { date_from: string; date_to: string } {
  if (start && end && start > end) throw new Error("开始日期不能晚于结束日期");
  const toISO = (value: string, nextDay = false) => {
    if (!value) return "";
    const [year, month, day] = value.split("-").map(Number);
    const date = new Date(year, month - 1, day);
    if (nextDay) date.setDate(date.getDate() + 1);
    return date.toISOString();
  };
  return { date_from: toISO(start), date_to: toISO(end, true) };
}
