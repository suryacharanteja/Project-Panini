declare module '../../../../../frontend/server/console-auth.mjs' {
  export function mintTicket(subject: string): Promise<string> | string;
}

declare module '../../../../../frontend/server/config.mjs' {
  export const DEFAULT_PORTS: {
    backend: number;
    gateway: number;
    search: number;
  };

  export function readConfig(): {
    ports?: Partial<typeof DEFAULT_PORTS>;
    [key: string]: unknown;
  };
}
