import { HAND_NAMES, LiveView, type LiveHand } from '../../../shared-ui';
import '../../../shared-ui/tokens.css';
import { harness, useResource } from '../api';
import { Empty, Page } from '../components/ui';

/** Every hand PANINI is using, live, with one-button take-over (V2-12). */
type Target = { hand: LiveHand; target: string; seq: number; viewers: number; url?: string };
type Holding = { hand: LiveHand; target: string };

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
      {(live.data?.holding.length ?? 0) > 0 && (
        <p className="mt-4 text-sm text-faint">
          You hold: {live.data!.holding.map((h) => HAND_NAMES[h.hand]).join(', ')}. PANINI waits on
          those.
        </p>
      )}
    </Page>
  );
}
