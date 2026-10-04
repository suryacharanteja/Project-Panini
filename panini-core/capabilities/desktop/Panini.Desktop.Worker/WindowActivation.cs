using System.Runtime.InteropServices;
using System.Text.Json.Nodes;

internal static partial class Program
{
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint current, uint other, bool attach);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr SetActiveWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr SetFocus(IntPtr hwnd);
    [StructLayout(LayoutKind.Sequential)] private struct ActivationMessage { public IntPtr Hwnd; public uint Id; public UIntPtr Wparam; public IntPtr Lparam; public uint Time; public int X, Y; public uint Reserved; }
    [DllImport("user32.dll")] private static extern bool PeekMessage(out ActivationMessage message, IntPtr hwnd, uint first, uint last, uint remove);

    // Called only for an explicitly selected and reviewed target; observation never activates it.
    private static void ActivateWindow(JsonObject request, IntPtr hwnd)
    {
        GroundedInput.EnsureIdle();
        Interactive(); Target(request);
        SetForegroundWindow(hwnd);
        if (!TargetForeground(hwnd))
        {
            PeekMessage(out _, IntPtr.Zero, 0, 0, 0);
            var current = GetCurrentThreadId();
            var foreground = GetWindowThreadProcessId(GetForegroundWindow(), out _);
            var targetThread = GetWindowThreadProcessId(hwnd, out _);
            var attached = foreground != 0 && foreground != current && AttachThreadInput(current, foreground, true);
            var targetAttached = targetThread != current && targetThread != foreground && AttachThreadInput(current, targetThread, true);
            try
            {
                Target(request);
                BringWindowToTop(hwnd); SetActiveWindow(hwnd); SetForegroundWindow(hwnd); SetFocus(hwnd);
            }
            finally
            {
                if (targetAttached) AttachThreadInput(current, targetThread, false);
                if (attached) AttachThreadInput(current, foreground, false);
            }
        }
        Target(request);
        if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED");
        if (request["expected_window_title"] is not null && Target(request).root.Current.Name != request["expected_window_title"]!.GetValue<string>())
            throw new InvalidOperationException("WINDOW_CHANGED");
    }
}
