using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;

internal static class Program
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct Startup { public int cb; public string? reserved, desktop, title; public int x, y, xs, ys, xc, yc, fill, flags; public short show, reserved2; public IntPtr reservedPtr, stdin, stdout, stderr; }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process, thread; public uint pid, tid; }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits { public long processTime, jobTime; public uint flags; public UIntPtr minWorking, maxWorking; public uint active; public UIntPtr affinity; public uint priority, scheduling; }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong a,b,c,d,e,f; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits { public BasicLimits basic; public IoCounters io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CreateProcess(string app, StringBuilder command, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string? cwd, ref Startup startup, out ProcessInfo info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll")] private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint size);
    [DllImport("kernel32.dll")] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll")] private static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll")] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll")] private static extern IntPtr GetStdHandle(int kind);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint current, uint other, bool attach);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool ShowWindowAsync(IntPtr hwnd, int command);
    [DllImport("user32.dll")] private static extern IntPtr SetActiveWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr SetFocus(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsChild(IntPtr parent, IntPtr child);
    [StructLayout(LayoutKind.Sequential)] private struct Message { public IntPtr hwnd; public uint id; public UIntPtr wparam; public IntPtr lparam; public uint time; public int x, y; public uint reserved; }
    [DllImport("user32.dll")] private static extern bool PeekMessage(out Message message, IntPtr hwnd, uint first, uint last, uint remove);

    private static string Quote(string value)
    {
        var result = new StringBuilder("\""); var slashes = 0;
        foreach (var ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes); slashes = 0; result.Append(ch);
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--focus")
        {
            try
            {
                var target = JsonNode.Parse(args[1])!;
                var hwnd = new IntPtr(target["hwnd"]!.GetValue<long>());
                GetWindowThreadProcessId(hwnd, out var pid);
                using var process = System.Diagnostics.Process.GetProcessById((int)pid);
                if (pid != target["pid"]!.GetValue<uint>() || process.StartTime.ToUniversalTime().Ticks.ToString() != target["created"]!.GetValue<string>()) return 74;
                if (process.SessionId != System.Diagnostics.Process.GetCurrentProcess().SessionId) return 74;
                if (target["hosted_pid"] is not null)
                {
                    var child = new IntPtr(target["hosted_hwnd"]!.GetValue<long>());
                    GetWindowThreadProcessId(child, out var hostedPid);
                    if (!IsChild(hwnd, child) || hostedPid != target["hosted_pid"]!.GetValue<uint>()) return 74;
                    using var hosted = System.Diagnostics.Process.GetProcessById((int)hostedPid);
                    if (hosted.StartTime.ToUniversalTime().Ticks.ToString() != target["hosted_created"]!.GetValue<string>()) return 74;
                }
                if (IsIconic(hwnd)) ShowWindowAsync(hwnd, 9);
                SetForegroundWindow(hwnd);
                if (GetForegroundWindow() != hwnd)
                {
                    // This short-lived helper is used only for the explicitly selected
                    // task window. Join its foreground input queue for activation;
                    // no keys or mouse events are synthesized. Always detach.
                    PeekMessage(out _, IntPtr.Zero, 0, 0, 0); // Establish this helper's input queue.
                    var current = GetCurrentThreadId();
                    var foreground = GetWindowThreadProcessId(GetForegroundWindow(), out _);
                    var targetThread = GetWindowThreadProcessId(hwnd, out _);
                    var attached = foreground != 0 && foreground != current && AttachThreadInput(current, foreground, true);
                    var targetAttached = targetThread != current && targetThread != foreground && AttachThreadInput(current, targetThread, true);
                    try { BringWindowToTop(hwnd); SetActiveWindow(hwnd); SetForegroundWindow(hwnd); SetFocus(hwnd); }
                    finally
                    {
                        if (targetAttached) AttachThreadInput(current, targetThread, false);
                        if (attached) AttachThreadInput(current, foreground, false);
                    }
                }
                Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(new { requested = hwnd.ToInt64(), foreground = GetForegroundWindow().ToInt64() }));
                return GetForegroundWindow() == hwnd ? 0 : 75;
            }
            catch { return 74; }
        }
        if (args.Length == 0 || !Path.IsPathFullyQualified(args[0])) return 64;
        var job = CreateJobObject(IntPtr.Zero, null);
        var limits = new ExtendedLimits { basic = new BasicLimits { flags = 0x2000 } }; // KILL_ON_JOB_CLOSE
        if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf<ExtendedLimits>())) return 70;
        var info = new ProcessInfo();
        try
        {
            var startup = new Startup { cb = Marshal.SizeOf<Startup>(), flags = 0x100, stdout = GetStdHandle(-11), stderr = GetStdHandle(-12) };
            var command = new StringBuilder(string.Join(' ', args.Select(Quote)));
            if (!CreateProcess(args[0], command, IntPtr.Zero, IntPtr.Zero, true, 0x08000004, IntPtr.Zero, null, ref startup, out info)) return 71;
            if (!AssignProcessToJobObject(job, info.process)) { TerminateProcess(info.process, 72); return 72; }
            ResumeThread(info.thread);
            var exited = Task.Run(() => WaitForSingleObject(info.process, uint.MaxValue));
            // The shell holds stdin open. EOF after a crash also closes the owned job.
            // Console readers can implement ReadToEndAsync synchronously. Keep that
            // blocking read off the owner thread so a child exit is observed at once.
            var disconnected = Task.Run(() => Console.In.ReadToEnd());
            await Task.WhenAny(exited, disconnected);
            if (exited.IsCompleted) { GetExitCodeProcess(info.process, out var code); return (int)code; }
            return 0;
        }
        finally
        {
            CloseHandle(job);
            if (info.process != IntPtr.Zero) CloseHandle(info.process);
            if (info.thread != IntPtr.Zero) CloseHandle(info.thread);
        }
    }
}
