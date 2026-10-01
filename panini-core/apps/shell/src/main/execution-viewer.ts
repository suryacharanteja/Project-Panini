import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, unlinkSync, writeFileSync } from 'node:fs';

// The terminal only displays a bounded projection. It never runs model output.
const viewerScript = `
$Host.UI.RawUI.WindowTitle = 'PANINI-CLI | Live execution viewer'
$iconApplied = $false
$iconError = ''
$iconExists = Test-Path -LiteralPath $env:PANINI_VIEWER_ICON
if ($iconExists) {
  try {
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class PaniniConsoleBrand { [DllImport("kernel32.dll")] public static extern bool SetConsoleIcon(IntPtr icon); [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam); }'
    $brandIcon = [Drawing.Icon]::new($env:PANINI_VIEWER_ICON)
    $iconApplied = [PaniniConsoleBrand]::SetConsoleIcon($brandIcon.Handle)
    $consoleWindow = [PaniniConsoleBrand]::GetConsoleWindow()
    if (!$iconApplied -and $consoleWindow -ne [IntPtr]::Zero) {
      $null = [PaniniConsoleBrand]::SendMessage($consoleWindow, 128, [IntPtr]::Zero, $brandIcon.Handle)
      $null = [PaniniConsoleBrand]::SendMessage($consoleWindow, 128, [IntPtr]1, $brandIcon.Handle)
      $iconApplied = [PaniniConsoleBrand]::SendMessage($consoleWindow, 127, [IntPtr]1, [IntPtr]::Zero) -eq $brandIcon.Handle
    }
  } catch { $iconError = $_.Exception.Message }
}
$screenOutput = [IO.StreamWriter]::new([IO.File]::Open('CONOUT$', [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite))
$screenOutput.AutoFlush = $true
$screenOutput.WriteLine('PANINI-CLI viewer connected. Waiting for activity...')
[IO.File]::WriteAllText($env:PANINI_VIEWER_READY, (@{ ready = $true; icon_applied = $iconApplied; icon_exists = $iconExists; icon_error = $iconError } | ConvertTo-Json -Compress))
$previous = ''
$owner = [Diagnostics.Process]::GetProcessById([int]$env:PANINI_VIEWER_OWNER)
$null = $owner.Handle
while (!$owner.HasExited -and (Test-Path -LiteralPath $env:PANINI_ACTIVITY_FILE) -and (Test-Path -LiteralPath $env:PANINI_VIEWER_ALIVE)) {
  try {
    $raw = [IO.File]::ReadAllText($env:PANINI_ACTIVITY_FILE)
    if ($raw -ne $previous) {
      $view = $raw | ConvertFrom-Json
      Clear-Host
      $screenOutput.WriteLine('PANINI-CLI | Read-only activity viewer')
      $screenOutput.WriteLine('Use PANINI to approve or stop. Closing this viewer does not stop work.')
      $screenOutput.WriteLine($view.text)
      $previous = $raw
    }
  } catch { }
  Start-Sleep -Milliseconds 300
}
exit
`;
const launchScript = `$viewer = Start-Process -FilePath "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -ArgumentList @('-NoLogo', '-NoProfile', '-EncodedCommand', $env:PANINI_VIEWER_SCRIPT) -WindowStyle Normal -PassThru; $viewer.WaitForExit()`;

export class ExecutionViewer {
  private task = '';
  private activity: any;
  private phase = 'running';
  private process?: ChildProcess;
  private autoOpened = '';
  private transcript = '';
  constructor(
    private file: () => string,
    private failure: (message: string) => void,
    private icon: () => string = () => '',
  ) {}

  update(event: any, auto: boolean) {
    if (event.type === 'code.activity') {
      this.task = event.task_id;
      this.activity = event.data;
      this.phase = 'running';
    } else if (event.type?.startsWith('task.') && event.task_id === this.task) {
      this.phase = event.type.slice(5);
      if (event.data?.execution) this.activity = event.data.execution;
    } else if (['runtime.error', 'controller.lost', 'session.closed'].includes(event.type)) {
      this.phase = 'disconnected';
    } else return;
    if (!this.activity) return;
    const text = [
      this.activity.workspace,
      'Model: ' + this.activity.model,
      'State: ' + this.phase,
      ...this.activity.items.map((item: any) =>
        [
          item.kind + ' · ' + item.status,
          item.command,
          ...(item.paths || []),
          item.output,
          item.exit_code != null ? 'Exit code: ' + item.exit_code : '',
        ]
          .filter(Boolean)
          .join('\n'),
      ),
      'Recent output only. Activity is not independent verification.',
    ].join('\n\n');
    this.transcript = text;
    try {
      writeFileSync(
        this.file(),
        JSON.stringify({
          text,
          finished: ['completed', 'stopped', 'blocked', 'failed', 'disconnected'].includes(
            this.phase,
          ),
        }),
        'utf8',
      );
      if (auto && this.autoOpened !== this.task) {
        this.autoOpened = this.task;
        void this.open().catch(() =>
          this.failure('Could not open PowerShell. Use the built-in output panel.'),
        );
      }
    } catch {
      this.failure('The PowerShell activity viewer is unavailable. Use the built-in output panel.');
    }
  }

  async open() {
    if (!this.activity) throw new Error('Start a coding task before opening its activity viewer.');
    if (this.process && this.process.exitCode === null && !this.process.killed) return;
    const readyFile = this.file() + '.viewer-ready';
    const aliveFile = this.file() + '.viewer-alive';
    if (existsSync(readyFile)) unlinkSync(readyFile);
    writeFileSync(aliveFile, 'alive');
    this.process = spawn(
      'powershell.exe',
      [
        '-NoLogo',
        '-NoProfile',
        '-EncodedCommand',
        Buffer.from(launchScript, 'utf16le').toString('base64'),
      ],
      {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          SystemRoot: process.env.SystemRoot,
          WINDIR: process.env.WINDIR,
          PATH: process.env.PATH,
          USERPROFILE: process.env.USERPROFILE,
          TEMP: process.env.TEMP,
          PANINI_ACTIVITY_FILE: this.file(),
          PANINI_VIEWER_READY: readyFile,
          PANINI_VIEWER_ALIVE: aliveFile,
          PANINI_VIEWER_SCRIPT: Buffer.from(viewerScript, 'utf16le').toString('base64'),
          PANINI_VIEWER_OWNER: String(process.pid),
          PANINI_VIEWER_ICON: this.icon(),
        },
      },
    );
    const child = this.process;
    child.stdout?.resume();
    let startupError = '';
    child.stderr?.on('data', (data) => {
      startupError = (startupError + data.toString()).slice(-1200);
    });
    await new Promise<void>((resolve, reject) => {
      const clear = () => {
        clearTimeout(timer);
        clearInterval(poll);
      };
      const timer = setTimeout(() => {
        clear();
        this.close();
        reject(new Error('PowerShell viewer did not open a console.'));
      }, 8000);
      const poll = setInterval(() => {
        if (existsSync(readyFile)) {
          clear();
          resolve();
        }
      }, 100);
      child.once('error', (error) => {
        clear();
        reject(error);
      });
      child.once('exit', () => {
        clear();
        reject(new Error('PowerShell viewer exited before becoming ready. ' + startupError));
      });
    });
    child.unref();
  }

  copyText() {
    return this.transcript;
  }
  close() {
    const aliveFile = this.file() + '.viewer-alive';
    if (existsSync(aliveFile)) unlinkSync(aliveFile);
    this.process?.kill();
  }
}
