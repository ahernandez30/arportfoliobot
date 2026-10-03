export function wsUrl(loc: Location = window.location): string {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`
}

/** Waits 1, 2, 4 … 30 seconds between reconnection attempts. */
export function backoffSeconds(attempt: number): number {
  return Math.min(30, 2 ** Math.max(0, attempt))
}
