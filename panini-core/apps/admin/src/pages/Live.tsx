import { HAND_NAMES, LiveView, type LiveHand } from '../../../shared-ui';
import '../../../shared-ui/tokens.css';
import { api, harness, useResource } from '../api';
import { Card, Empty, Page, Switch } from '../components/ui';

/** Every hand PANINI is using, live, with one-button take-over (V2-12). */
type Target = {
  hand: LiveHand;
  target: string;
  seq: number;
  viewers: number;
  url?: string;
  watchedRemotely?: boolean;
};
type Holding = { hand: LiveHand; target: string };
type Grants = Record<LiveHand, boolean>;
const GRANT_HELP: Partial<Record<LiveHand, string>> = {
  chrome: 'Your own Chrome tabs are personal: off until you turn it on.',
  desktop: 'Your desktop is personal: off until you turn it on.',
  managed: "PANINI's own browser.",
  sandbox: "PANINI's isolated computer.",
};

/** Which hands your phone and other remote surfaces may watch and control (V2-12 §5.6). */
function RemoteGrants() {
  const grants = useResource<Grants>(harness('/live/grants'));
  if (!grants.data) return null;
  return (
    <Card title="Remote viewing">
      <div className="grid gap-3 sm:grid-cols-2">
        {(['managed', 'sandbox', 'chrome', 'desktop'] as LiveHand[]).map((hand) => (
          <label key={hand} className="flex items-start gap-3 text-sm">
            <Switch
              checked={grants.data![hand]}
              label={HAND_NAMES[hand]}
              onChange={async (value) => {
                await api(harness('/live/grants'), { method: 'PATCH', body: { [hand]: value } });
                grants.reload();
              }}
            />
            <span>
              <span className="text-slate">{HAND_NAMES[hand]}</span>
              <span className="block text-xs text-faint">{GRANT_HELP[hand]}</span>
            </span>
          </label>
        ))}
      </div>
    </Card>
  );
}

export function LivePage() {
  const live = useResource<{ targets: Target[]; holding: Holding[] }>(harness('/live'), 3000);
  if (live.error) {
    return (
      <Page title="Live" deva="दृश्य" eyebrow="Watch and take over">
        <Empty>
          Live view is off. Set PANINI_LIVE_VIEW=true to watch and take over PANINI's hands.
        </Empty>
      </Page>
    );
  }
  const targets = live.data?.targets ?? [];
  return (
    <Page title="Live" deva="दृश्य" eyebrow="Watch and take over">
      {targets.length === 0 ? (
        <Empty>
          Nothing to watch yet. Frames appear while PANINI uses a browser, the sandbox or the
          desktop.
        </Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {targets.map((t) => (
            <LiveView
              key={`${t.hand}/${t.target}`}
              base={harness('')}
              hand={t.hand}
              target={t.target}
            />
          ))}
        </div>
      )}
      {targets
        .filter((t) => t.watchedRemotely && (t.hand === 'chrome' || t.hand === 'desktop'))
        .map((t) => (
          <p key={`w-${t.hand}`} className="mt-4 text-sm text-copper">
            Watching from your phone: {HAND_NAMES[t.hand]}.
          </p>
        ))}
      <div className="mt-6">
        <RemoteGrants />
      </div>
      {(live.data?.holding.length ?? 0) > 0 && (
        <p className="mt-4 text-sm text-faint">
          You hold: {live.data!.holding.map((h) => HAND_NAMES[h.hand]).join(', ')}. PANINI waits on
          those.
        </p>
      )}
    </Page>
  );
}
