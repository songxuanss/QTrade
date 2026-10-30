/** Convert calendar dates in a declared source timezone, including DST day length. */
function midnight(day: string, timezone: string): number {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw Error("invalid_input");
  const target = Date.parse(day + "T00:00:00Z");
  if (
    !Number.isFinite(target) ||
    new Date(target).toISOString().slice(0, 10) !== day
  )
    throw Error("invalid_input");
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  let result = target;
  for (let i = 0; i < 3; i++) {
    const parts = Object.fromEntries(
      formatter.formatToParts(result).map((part) => [part.type, part.value]),
    );
    const observed = Date.parse(
      `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`,
    );
    result += target - observed;
  }
  return result;
}
export function collectionDates(first: string, last: string, timezone: string) {
  const start = midnight(first, timezone);
  midnight(last, timezone);
  const next = new Date(Date.parse(last + "T00:00:00Z") + 86400_000)
    .toISOString()
    .slice(0, 10);
  const end = midnight(next, timezone);
  if (start >= end) throw Error("invalid_input");
  return {
    start: new Date(start).toISOString(),
    end: new Date(end).toISOString(),
  };
}
