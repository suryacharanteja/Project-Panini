using System.Runtime.InteropServices;

internal static class Native
{
    [StructLayout(LayoutKind.Sequential)] private struct MouseInput { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Sequential)] private struct Input { public uint Type; public MouseInput Mouse; }
    [DllImport("user32.dll")] private static extern uint SendInput(uint count, Input[] inputs, int size);
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr context);

    /// <summary>Physical pixels everywhere, matching the worker's coordinates and the overlay's placement.</summary>
    public static void PerMonitorDpi() => SetProcessDpiAwarenessContext(new IntPtr(-4));

    /// <summary>
    /// If a worker is killed (Stop, deadline, crash) while it holds a synthetic button down, release it.
    /// Releasing a button that is not down is harmless.
    /// </summary>
    public static void ReleaseMouse()
    {
        var release = new[] { 4u, 16u, 64u }.Select(flag => new Input { Type = 0, Mouse = new MouseInput { Flags = flag } }).ToArray();
        SendInput((uint)release.Length, release, Marshal.SizeOf<Input>());
    }
}
