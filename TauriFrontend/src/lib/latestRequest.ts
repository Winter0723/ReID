export interface RequestTicket {
  key: string;
  version: number;
  signal: AbortSignal;
}

export interface LatestRequestGate {
  begin(key: string): RequestTicket;
  isCurrent(ticket: RequestTicket): boolean;
  invalidate(key: string): void;
}

export function createLatestRequestGate(): LatestRequestGate {
  const versions = new Map<string, number>();
  const controllers = new Map<string, AbortController>();

  function nextVersion(key: string): number {
    const version = (versions.get(key) || 0) + 1;
    versions.set(key, version);
    return version;
  }

  return {
    begin(key) {
      controllers.get(key)?.abort();
      const controller = new AbortController();
      controllers.set(key, controller);
      return { key, version: nextVersion(key), signal: controller.signal };
    },
    isCurrent(ticket) {
      return !ticket.signal.aborted && versions.get(ticket.key) === ticket.version;
    },
    invalidate(key) {
      controllers.get(key)?.abort();
      controllers.delete(key);
      nextVersion(key);
    },
  };
}
