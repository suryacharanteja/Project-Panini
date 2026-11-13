using System.Runtime.InteropServices;
using System.Text;

// Genuine Win32 controls, providing a second UIA implementation beside the WPF fixture.
internal static class Win32Fixture
{
    private delegate IntPtr WindowProc(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct WindowClass
    {
        public uint Size, Style;
        public WindowProc Proc;
        public int ClassExtra, WindowExtra;
        public IntPtr Instance, Icon, Cursor, Background;
        public string? Menu;
        public string ClassName;
        public IntPtr SmallIcon;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Message { public IntPtr Hwnd; public uint Id; public IntPtr Wparam, Lparam; public uint Time; public int X, Y; public uint Private; }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern ushort RegisterClassEx(ref WindowClass info);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr CreateWindowEx(uint extended, string cls, string title, uint style, int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr DefWindowProc(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] private static extern int GetMessage(out Message message, IntPtr hwnd, uint min, uint max);
    [DllImport("user32.dll")] private static extern bool TranslateMessage(ref Message message);
    [DllImport("user32.dll")] private static extern IntPtr DispatchMessage(ref Message message);
    [DllImport("user32.dll")] private static extern void PostQuitMessage(int code);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool SetWindowText(IntPtr hwnd, string value);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr GetModuleHandle(string? module);
    private static readonly WindowProc Callback = WndProc;
    private static IntPtr draft, status;

    public static void Run()
    {
        var instance = GetModuleHandle(null);
        var info = new WindowClass { Size = (uint)Marshal.SizeOf<WindowClass>(), Proc = Callback,
            Instance = instance, Background = new IntPtr(6), ClassName = "PaniniWin32Fixture" };
        if (RegisterClassEx(ref info) == 0) throw new InvalidOperationException("Win32 fixture class failed");
        var hwnd = CreateWindowEx(0, info.ClassName, "PANINI Win32 verification fixture", 0x10CF0000,
            320, 180, 480, 340, IntPtr.Zero, IntPtr.Zero, instance, IntPtr.Zero);
        if (hwnd == IntPtr.Zero) throw new InvalidOperationException("Win32 fixture window failed");
        var result = 0;
        while ((result = GetMessage(out var message, IntPtr.Zero, 0, 0)) > 0)
        {
            TranslateMessage(ref message);
            DispatchMessage(ref message);
        }
        if (result < 0) throw new InvalidOperationException("Win32 fixture message loop failed");
    }

    private static IntPtr WndProc(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam)
    {
        if (message == 1)
        {
            IntPtr Child(string cls, string text, uint style, int y, int id) => CreateWindowEx(0, cls, text,
                0x50000000 | style, 24, y, 400, 30, hwnd, new IntPtr(id), GetModuleHandle(null), IntPtr.Zero);
            Child("STATIC", "Draft text", 0, 20, 100);
            draft = Child("EDIT", "", 0x00810080, 50, 101);
            Child("BUTTON", "Apply draft", 0x00010000, 90, 102);
            status = Child("STATIC", "No action performed", 0, 130, 103);
            Child("STATIC", "Private test field", 0, 170, 104);
            Child("EDIT", "fixture-secret", 0x008100A0, 200, 105);
            Child("BUTTON", "Enable fixture option", 0x00010003, 240, 106);
            return IntPtr.Zero;
        }
        if (message == 0x111 && (wparam.ToInt64() & 0xFFFF) == 102)
        {
            var value = new StringBuilder(4001);
            GetWindowText(draft, value, value.Capacity);
            SetWindowText(status, "Applied: " + value);
            return IntPtr.Zero;
        }
        if (message == 2) { PostQuitMessage(0); return IntPtr.Zero; }
        return DefWindowProc(hwnd, message, wparam, lparam);
    }
}
