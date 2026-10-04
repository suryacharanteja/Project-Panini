import { Button, inputClass } from '../components/ui';

export type AvailabilityDraft = { server: string; tool: string; start: string; end: string };
export type LookupSource = { server: string; tool: string };
export function localTime(value: string) {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function AvailabilityInputs({
  checks,
  sources,
  change,
}: {
  checks: AvailabilityDraft[];
  sources: LookupSource[];
  change: (value: AvailabilityDraft[]) => void;
}) {
  const edit = (index: number, value: Partial<AvailabilityDraft>) =>
    change(checks.map((item, position) => (position === index ? { ...item, ...value } : item)));
  return (
    <fieldset className="grid gap-3">
      <legend>Permitted availability checks</legend>
      <p className="text-xs text-faint">
        Optional: approving the brief permits PANINI to share only free/busy for these exact
        windows. No event details or bookings. Discover the availability-only tool in Connectors and
        grant background read access first.
      </p>
      {checks.map((item, index) => (
        <div key={index} className="grid gap-2 rounded-lg border border-line p-3">
          <label>
            Availability source {index + 1}
            <select
              className={inputClass}
              value={`${item.server}/${item.tool}`}
              required
              onChange={(event) => {
                const source = sources.find(
                  (value) => `${value.server}/${value.tool}` === event.target.value,
                );
                if (source) edit(index, source);
              }}
            >
              {!sources.some(
                (source) => source.server === item.server && source.tool === item.tool,
              ) && <option value={`${item.server}/${item.tool}`}>Source unavailable</option>}
              {sources.map((source) => (
                <option
                  key={`${source.server}/${source.tool}`}
                  value={`${source.server}/${source.tool}`}
                >
                  {source.server} · {source.tool}
                </option>
              ))}
            </select>
          </label>
          {(['start', 'end'] as const).map((key) => (
            <label key={key}>
              Availability {key} {index + 1} (your local timezone)
              <input
                className={inputClass}
                type="datetime-local"
                required
                value={item[key]}
                onChange={(event) => edit(index, { [key]: event.target.value })}
              />
            </label>
          ))}
          <Button onClick={() => change(checks.filter((_, position) => position !== index))}>
            Remove availability window {index + 1}
          </Button>
        </div>
      ))}
      <Button
        disabled={!sources.length || checks.length >= 3}
        onClick={() => {
          const source = sources[0];
          if (source) change([...checks, { ...source, start: '', end: '' }]);
        }}
      >
        Add availability window
      </Button>
      {!sources.length && (
        <p className="text-xs text-faint">
          No granted availability-only source is currently available.
        </p>
      )}
    </fieldset>
  );
}
