import { useEffect, useState } from 'react';
import { LiveView } from '../../../shared-ui';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, inputClass, Page, useAction } from '../components/ui';

type Status = {
  serial: string;
  driver_consent: boolean;
  allowed_apps: string[];
  enabled: boolean;
  adb: string | null;
  qualification: string;
};
type Device = { serial: string; model: string; state: string };
const path = (suffix = '') => harness('/android' + suffix);

export function AndroidPage() {
  const status = useResource<Status>(path());
  const [devices, setDevices] = useState<Device[]>([]);
  const [serial, setSerial] = useState('');
  const [consent, setConsent] = useState(false);
  const [apps, setApps] = useState('');
  const [pairAddress, setPairAddress] = useState('');
  const [connectAddress, setConnectAddress] = useState('');
  const [code, setCode] = useState('');
  const [watching, setWatching] = useState(false);
  const [download, setDownload] = useState<{
    name: string;
    bytes: number;
    url: string;
    license: string;
  } | null>(null);
  const [sdkConsent, setSdkConsent] = useState(false);
  const action = useAction();
  useEffect(() => {
    if (status.data) {
      setSerial(status.data.serial);
      setConsent(status.data.driver_consent);
      setApps(status.data.allowed_apps.join('\n'));
    }
  }, [status.data]);
  return (
    <Page title="Android hands" eyebrow="A phone PANINI can operate">
      <Card title="Connection">
        <div className="grid gap-4">
          <div className="flex gap-2">
            <Badge tone={status.data?.enabled ? 'ok' : 'neutral'}>
              {status.data?.enabled ? 'Enabled' : 'Off'}
            </Badge>
            <span className="text-sm text-faint">
              {status.data?.adb ? 'ADB detected' : 'Android SDK Platform Tools required'}
            </span>
          </div>
          <p className="text-sm text-faint">
            Enable PANINI_ANDROID and PANINI_V2_LEDGER in Settings. On your phone, enable Developer
            options → Wireless debugging, then pair below. USB debugging also works after you
            authorize this PC on the phone.
          </p>
          {status.data && !status.data.adb && (
            <div className="grid gap-2 text-sm">
              <Button
                disabled={!!action.busy}
                onClick={() =>
                  void action.run('download', async () => {
                    setDownload(await api(path('/download')));
                  })
                }
              >
                Check official Platform Tools download
              </Button>
              {download && (
                <>
                  <p>
                    {download.name} · {(download.bytes / 1048576).toFixed(1)} MiB · Google download
                  </p>
                  <a
                    className="text-copper"
                    href={download.license}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Read the Android SDK license and release notes
                  </a>
                  <label className="flex gap-2">
                    <input
                      type="checkbox"
                      checked={sdkConsent}
                      onChange={(e) => setSdkConsent(e.target.checked)}
                    />
                    I have read and accept Google's Android SDK license and want to download
                    Platform Tools.
                  </label>
                  {sdkConsent && (
                    <a className="text-copper" href={download.url}>
                      Download Platform Tools for Windows
                    </a>
                  )}
                  <p className="text-faint">
                    Extract the platform-tools folder into %LOCALAPPDATA%\PANINI, then refresh this
                    page. An existing Android Studio SDK also works.
                  </p>
                </>
              )}
            </div>
          )}
          <form
            className="grid gap-2 sm:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              void action.run('pair', async () => {
                try {
                  return await api(path('/pair'), { body: { address: pairAddress, code } });
                } finally {
                  setCode('');
                }
              });
            }}
          >
            <input
              className={inputClass}
              aria-label="Pairing address"
              placeholder="Pairing IP:port"
              value={pairAddress}
              onChange={(e) => setPairAddress(e.target.value)}
            />
            <input
              className={inputClass}
              aria-label="Pairing code"
              type="password"
              autoComplete="off"
              inputMode="numeric"
              maxLength={6}
              placeholder="Six-digit pairing code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <Button type="submit" disabled={!!action.busy || !/^\d{6}$/.test(code)}>
              Pair phone
            </Button>
          </form>
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void action.run('connect', () =>
                api(path('/connect'), { body: { address: connectAddress } }),
              );
            }}
          >
            <input
              className={inputClass}
              aria-label="Debugging address"
              placeholder="Debugging IP:port (different from pairing port)"
              value={connectAddress}
              onChange={(e) => setConnectAddress(e.target.value)}
            />
            <Button type="submit" disabled={!!action.busy || !connectAddress}>
              Connect
            </Button>
          </form>
          <Button
            disabled={!!action.busy}
            onClick={() =>
              void action.run('devices', async () => {
                const result = await api<{ devices: Device[] }>(path('/devices'));
                setDevices(result.devices);
              })
            }
          >
            Find connected phones
          </Button>
          <select
            className={inputClass}
            aria-label="Selected Android phone"
            value={serial}
            onChange={(e) => setSerial(e.target.value)}
          >
            <option value="">Select a phone</option>
            {serial && !devices.some((d) => d.serial === serial) && (
              <option value={serial}>{serial} (saved)</option>
            )}
            {devices.map((d) => (
              <option key={d.serial} value={d.serial} disabled={d.state !== 'device'}>
                {d.model} · {d.serial} · {d.state}
              </option>
            ))}
          </select>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            I allow Playwright to install its small automation driver apps on the selected phone.
            Android debugging remains visible on the phone; I can stop control below or disable
            debugging on the phone.
          </label>
          <label className="grid gap-2 text-sm">
            Allowed app packages (one per line)
            <textarea
              className={inputClass}
              value={apps}
              onChange={(e) => setApps(e.target.value)}
              placeholder="Add only apps PANINI may use. Unknown apps, banks, payment apps and account settings require an explicit allowance."
            />
          </label>
          <p className="text-xs text-faint">
            Password and security-code fields remain reserved for you. Actions still follow your
            permissions and approval rules.
          </p>
          <div className="flex gap-2">
            <Button
              disabled={!!action.busy || !serial}
              onClick={() =>
                void action.run('save', async () => {
                  await api(path('/settings'), {
                    body: {
                      serial,
                      driver_consent: consent,
                      allowed_apps: apps.split(/\s+/).filter(Boolean),
                    },
                  });
                  await status.reload();
                })
              }
            >
              Save setup
            </Button>
            <Button
              disabled={!!action.busy || !status.data?.serial}
              onClick={() => void action.run('stop', () => api(path('/stop'), { body: {} }))}
            >
              Stop phone control
            </Button>
          </div>
          {status.error && <p role="alert">{status.error}</p>}
          <p className="text-xs text-faint">{status.data?.qualification}</p>
        </div>
      </Card>
      {status.data?.serial && status.data.enabled && (
        <div className="mt-6">
          <Button onClick={() => setWatching(!watching)}>
            {watching ? 'Close live view' : 'Watch phone'}
          </Button>
          {watching && (
            <div className="mt-4">
              <LiveView base={harness('')} hand="android" target={status.data.serial} />
            </div>
          )}
        </div>
      )}
    </Page>
  );
}
