import { harness, useResource } from '../api';
import { Badge, Card, Empty, Page } from '../components/ui';

/** Safety (V2-13): the red-team gate, surfaces it holds off, policy disagreements and tainted memories. */
type Safety = {
  guardian: boolean;
  gate: { passed: boolean; reason: string; result?: { cases?: number; completion?: number } };
  blocked: Record<string, string>;
  shadow: { more_permissive: number; stricter: number };
  taintedMemories: { id: string; kind: string; text: string; created: number }[];
};

const SURFACES: Record<string, string> = {
  PANINI_REMOTE_MODE: 'Remote access',
  PANINI_PHONE_LINE: 'Phone line',
  PANINI_CHANNELS: 'Channels',
  PANINI_MCP_SERVER: 'MCP server',
};

export function SafetyPage() {
  const safety = useResource<Safety>(harness('/safety'), 10000);
  const data = safety.data;
  return (
    <Page title="Safety" deva="रक्षा" eyebrow="Guarding what PANINI does">
      {!data ? (
        <Empty>Loading safety status…</Empty>
      ) : (
        <div className="grid gap-4">
          <Card
            title={
              <span className="flex items-center gap-2">
                Red-team gate{' '}
                <Badge tone={data.gate.passed ? 'ok' : 'warn'}>
                  {data.gate.passed ? 'passed' : 'not met'}
                </Badge>
              </span>
            }
          >
            <p className="text-sm text-slate">
              {data.gate.passed
                ? `The latest live run passed (${data.gate.result?.cases ?? 0} cases).`
                : data.gate.reason}
            </p>
            <p className="mt-2 text-xs text-faint">
              Remote access, the phone line, channels and the MCP server stay off until this passes.
            </p>
            {Object.keys(data.blocked).length > 0 && (
              <ul className="mt-3 grid gap-1 text-sm">
                {Object.entries(data.blocked).map(([flag, reason]) => (
                  <li key={flag}>
                    <strong>{SURFACES[flag] ?? flag}</strong> is turned on in settings but held off:{' '}
                    {reason}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Policy decisions">
            <p className="text-sm text-slate">
              Where the v2 policy and the v1 rules disagreed: {data.shadow.stricter} times v2 was
              stricter, {data.shadow.more_permissive} times it was more permissive.
            </p>
          </Card>
          <Card title="Memories saved after reading untrusted content">
            {data.taintedMemories.length === 0 ? (
              <p className="text-sm text-faint">
                None. Such memories always need your approval and are listed here.
              </p>
            ) : (
              <ul className="grid gap-1 text-sm">
                {data.taintedMemories.map((m) => (
                  <li key={m.id}>
                    <Badge tone="neutral">{m.kind}</Badge> {m.text}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}
