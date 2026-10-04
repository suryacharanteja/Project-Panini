// Adapted from pi-computer-use input.rs. Copyright (c) 2026 Zane Chee.
// Adapted from pi-computer-use 0.5.1 (MIT); see licenses/pi-computer-use.MIT.txt.
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;

internal static class GroundedInput
{
    [StructLayout(LayoutKind.Sequential)] private struct Mouse { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Sequential)] private struct Keyboard { public ushort Key, Scan; public uint Flags, Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Explicit)] private struct Data { [FieldOffset(0)] public Mouse Mouse; [FieldOffset(0)] public Keyboard Keyboard; }
    [StructLayout(LayoutKind.Sequential)] private struct Input { public uint Type; public Data Data; }
    [DllImport("user32.dll")] private static extern uint SendInput(uint count, Input[] inputs, int size);
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(NativePoint point);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [StructLayout(LayoutKind.Sequential)] private struct NativePoint { public int X, Y; }

    private static Input Key(ushort key, ushort scan, uint flags) => new() { Type = 1, Data = new Data { Keyboard = new Keyboard { Key = key, Scan = scan, Flags = flags } } };
    private static Input MouseEvent(uint flags, int x = 0, int y = 0, int data = 0) => new() { Data = new Data { Mouse = new Mouse { Flags = flags, X = x, Y = y, Data = unchecked((uint)data) } } };

    private static Input Absolute(int x, int y)
    {
        var left = GetSystemMetrics(76); var top = GetSystemMetrics(77);
        var width = GetSystemMetrics(78); var height = GetSystemMetrics(79);
        if (width <= 1 || height <= 1 || x < left || y < top || x >= left + width || y >= top + height)
            throw new InvalidOperationException("POINT_OUTSIDE_DESKTOP");
        return MouseEvent(0x8000 | 0x4000 | 0x2000 | 1,
            PointerGeometry.Normalize(x, left, width), PointerGeometry.Normalize(y, top, height));
    }

    [DllImport("user32.dll")] private static extern bool GetCursorPos(out NativePoint point);
    [DllImport("winmm.dll")] private static extern uint timeBeginPeriod(uint milliseconds);

    private static (uint Down, uint Up, int Data) ButtonFlags(string button) => button switch
    {
        "left" => (2u, 4u, 0), "right" => (8u, 16u, 0), "wheel" or "middle" => (32u, 64u, 0),
        "back" => (128u, 256u, 1), "forward" => (128u, 256u, 2),
        _ => throw new InvalidOperationException("BUTTON_UNSUPPORTED")
    };

    private static (int X, int Y) Cursor() { GetCursorPos(out var point); return (point.X, point.Y); }

    private static void SendOne(Input input)
    {
        if (SendInput(1, [input], Marshal.SizeOf<Input>()) != 1) throw new InvalidOperationException("UNKNOWN_EFFECT");
    }

    private static void MoveTo(int x, int y) => SendOne(Absolute(x, y));

    /// <summary>
    /// Travel the real pointer to a point with easing, in small timed moves, the way a person would.
    /// Applications receive genuine hover and move events, and PANINI's cursor overlay follows it.
    /// </summary>
    public static void Glide(int x, int y, Action validate)
    {
        var (startX, startY) = Cursor();
        var distance = Math.Sqrt(Math.Pow(x - startX, 2) + Math.Pow(y - startY, 2));
        if (distance < 4) { MoveTo(x, y); return; }
        var steps = Math.Max(3, PointerGeometry.GlideMilliseconds(distance) / 8);
        for (var step = 1; step <= steps; step++)
        {
            var t = PointerGeometry.EaseInOut((double)step / steps);
            MoveTo(startX + (int)Math.Round((x - startX) * t), startY + (int)Math.Round((y - startY) * t));
            Thread.Sleep(8);
            if (step % 8 == 0) validate();
        }
        MoveTo(x, y);
    }

    /// <summary>A person moving the mouse while PANINI holds it always wins.</summary>
    private static void CheckTakeover((int X, int Y) expected)
    {
        var (x, y) = Cursor();
        if (Math.Abs(x - expected.X) > 24 || Math.Abs(y - expected.Y) > 24)
            throw new InvalidOperationException("USER_INPUT_ACTIVE");
    }

    public static void Pointer(IReadOnlyList<(int X, int Y)> points, string kind, string button, int scrollX, int scrollY, Action validate)
    {
        var (down, up, data) = ButtonFlags(button);
        if (kind is not ("click" or "double_click" or "move" or "scroll")) throw new InvalidOperationException("UNSUPPORTED_ACTION");
        EnsureIdle();
        validate();
        timeBeginPeriod(1);
        var pressed = false;
        try
        {
            Glide(points[0].X, points[0].Y, validate);
            if (kind == "move") { Thread.Sleep(180); return; }
            Thread.Sleep(60); // Let hover/enter events reach the application before the press.
            validate();
            if (kind is "click" or "double_click")
                for (var i = 0; i < (kind == "double_click" ? 2 : 1); i++)
                {
                    if (i > 0) Thread.Sleep(70);
                    SendOne(MouseEvent(down, data: data)); pressed = true;
                    Thread.Sleep(70); // A visible, registered press rather than a zero-length one.
                    SendOne(MouseEvent(up, data: data)); pressed = false;
                }
            else
            {
                // Signed wheel units: +Y scrolls down, +X scrolls right. 120 = one detent.
                if (scrollY != 0) SendOne(MouseEvent(0x800, data: -scrollY));
                if (scrollX != 0) SendOne(MouseEvent(0x1000, data: scrollX));
            }
        }
        finally
        {
            // Never leave a synthetic button held if cancellation or focus loss occurs.
            if (pressed) SendInput(1, [MouseEvent(up, data: data)], Marshal.SizeOf<Input>());
        }
    }

    /// <summary>
    /// Press-drag-release along each stroke, lifting between strokes. Moves are dense and timed so
    /// painting and drawing applications register a continuous line. Progress reports (stroke, strokes).
    /// </summary>
    public static void Draw(IReadOnlyList<List<(int X, int Y)>> strokes, string button, int stepMs, Action validate, Action<int, int>? progress = null)
    {
        var (down, up, data) = ButtonFlags(button);
        EnsureIdle();
        validate();
        timeBeginPeriod(1);
        var pressed = false;
        try
        {
            for (var stroke = 0; stroke < strokes.Count; stroke++)
            {
                var path = PointerGeometry.Densify(strokes[stroke], 4);
                progress?.Invoke(stroke + 1, strokes.Count);
                Glide(path[0].X, path[0].Y, validate); // Pen up: travel without drawing.
                Thread.Sleep(45);
                validate();
                SendOne(MouseEvent(down, data: data)); pressed = true;
                Thread.Sleep(55);
                var last = path[0];
                for (var index = 1; index < path.Count; index++)
                {
                    MoveTo(path[index].X, path[index].Y);
                    last = path[index];
                    Thread.Sleep(stepMs);
                    if (index % 10 == 0) { validate(); CheckTakeover(last); }
                }
                Thread.Sleep(35);
                SendOne(MouseEvent(up, data: data)); pressed = false;
                Thread.Sleep(50);
            }
        }
        finally
        {
            if (pressed) SendInput(1, [MouseEvent(up, data: data)], Marshal.SizeOf<Input>());
        }
    }

    public static void Chord(string[] keys, Action validate)
    {
        if (keys.Length is < 1 or > 6) throw new InvalidOperationException("KEY_UNSUPPORTED");
        var map = new Dictionary<string, ushort>(StringComparer.OrdinalIgnoreCase)
        {
            ["CTRL"] = 17, ["CONTROL"] = 17, ["ALT"] = 18, ["SHIFT"] = 16, ["WIN"] = 91, ["META"] = 91,
            ["ENTER"] = 13, ["RETURN"] = 13, ["TAB"] = 9, ["ESC"] = 27, ["ESCAPE"] = 27,
            ["SPACE"] = 32, ["BACKSPACE"] = 8, ["DELETE"] = 46, ["INSERT"] = 45,
            ["UP"] = 38, ["DOWN"] = 40, ["LEFT"] = 37, ["RIGHT"] = 39,
            ["HOME"] = 36, ["END"] = 35, ["PAGEUP"] = 33, ["PAGEDOWN"] = 34
        };
        var codes = keys.Select(key => map.TryGetValue(key, out var vk) ? vk :
            key.Length == 1 && char.IsAsciiLetterOrDigit(key[0]) ? (ushort)char.ToUpperInvariant(key[0]) :
            key.StartsWith("F", StringComparison.OrdinalIgnoreCase) && int.TryParse(key[1..], out var fn) && fn is >= 1 and <= 24 ? (ushort)(111 + fn) :
            throw new InvalidOperationException("KEY_UNSUPPORTED")).ToArray();
        if (codes.Distinct().Count() != codes.Length) throw new InvalidOperationException("DUPLICATE_KEY");
        uint Flags(ushort vk) => vk is >= 33 and <= 46 or 91 ? 1u : 0u;
        var releases = codes.Reverse().Select(vk => Key(vk, 0, Flags(vk) | 2)).ToArray();
        Send(codes.Select(vk => Key(vk, 0, Flags(vk))).Concat(releases).ToArray(), releases, validate);
    }

    public static void Unicode(string text, Action validate)
    {
        if (text.Length is < 1 or > 4000 || text.Any(c => char.IsControl(c) && c is not '\r' and not '\n' and not '\t'))
            throw new InvalidOperationException("INPUT_TEXT_UNSUPPORTED");
        text = text.Replace("\r\n", "\n").Replace('\r', '\n');
        var events = text.SelectMany(c => c is '\n' or '\t'
            ? new[] { Key((ushort)(c == '\n' ? 13 : 9), 0, 0), Key((ushort)(c == '\n' ? 13 : 9), 0, 2) }
            : new[] { Key(0, c, 4), Key(0, c, 6) }).ToArray();
        Send(events, events.Where(e => (e.Data.Keyboard.Flags & 2) != 0).ToArray(), validate);
    }

    public static void EnsureIdle()
    {
        // Never combine synthetic input with a user's held button or modifier.
        foreach (var key in new[] { 1, 2, 4, 16, 17, 18, 91, 92 })
            if ((GetAsyncKeyState(key) & 0x8000) != 0) throw new InvalidOperationException("USER_INPUT_ACTIVE");
    }

    private static void Send(Input[] inputs, Input[] releases, Action validate)
    {
        EnsureIdle();
        validate();
        // One bounded batch includes every down/up pair. No keys/buttons remain
        // held across IPC calls, waits, confirmations or worker cancellation.
        if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<Input>()) != inputs.Length)
        {
            SendInput((uint)releases.Length, releases, Marshal.SizeOf<Input>());
            throw new InvalidOperationException("UNKNOWN_EFFECT");
        }
    }

    public static void Click(IntPtr window, AutomationElement element, Action validate, int clickCount = 1, Action<int, int>? boundsHit = null)
    {
        var observedBounds = element.Current.BoundingRectangle;
        Point point;
        if (!element.TryGetClickablePoint(out point))
        {
            // Some standard WPF edit providers expose a valid visible rectangle but
            // omit UIA's optional clickable-point property. The fallback remains
            // element-grounded and is checked for occlusion before dispatch.
            var bounds = element.Current.BoundingRectangle;
            if (bounds.IsEmpty || bounds.Width <= 1 || bounds.Height <= 1)
                throw new InvalidOperationException("ELEMENT_NOT_CLICKABLE");
            point = new Point(bounds.Left + bounds.Width / 2, bounds.Top + bounds.Height / 2);
        }
        var x = checked((int)Math.Floor(point.X));
        var y = checked((int)Math.Floor(point.Y));
        if (GetAncestor(WindowFromPoint(new NativePoint { X = x, Y = y }), 2) != window)
            throw new InvalidOperationException("TARGET_OCCLUDED");
        var left = GetSystemMetrics(76); var top = GetSystemMetrics(77);
        var width = GetSystemMetrics(78); var height = GetSystemMetrics(79);
        if (width <= 1 || height <= 1 || x < left || y < top || x >= left + width || y >= top + height)
            throw new InvalidOperationException("POINT_OUTSIDE_DESKTOP");
        var move = Absolute(x, y);
        var count = Math.Clamp(clickCount, 1, 2);
        var events = new List<Input> { move };
        for (var i = 0; i < count; i++) events.AddRange([MouseEvent(2), MouseEvent(4)]);
        void ClickCheck()
        {
            validate();
            if (element.Current.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
            if (!element.Current.IsEnabled || element.Current.IsOffscreen || element.Current.BoundingRectangle != observedBounds)
                throw new InvalidOperationException("ELEMENT_CHANGED");
            if (GetAncestor(WindowFromPoint(new NativePoint { X = x, Y = y }), 2) != window)
                throw new InvalidOperationException("TARGET_OCCLUDED");
            if (boundsHit is not null) { boundsHit(x, y); return; }
            var hit = AutomationElement.FromPoint(new Point(x, y));
            for (var i = 0; hit is not null && i < 64; i++, hit = TreeWalker.RawViewWalker.GetParent(hit))
            {
                if (hit.Current.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
                if (Automation.Compare(hit, element)) return;
            }
            throw new InvalidOperationException("ELEMENT_OCCLUDED");
        }
        if (boundsHit is null) Send(events.ToArray(), [MouseEvent(4)], ClickCheck);
        else Pointer(new[] { (x, y) }, count == 2 ? "double_click" : "click", "left", 0, 0, ClickCheck);
    }

    public static void Move(int x, int y, Action validate)
    {
        EnsureIdle(); validate();
        if (!SetCursorPos(x, y)) throw new InvalidOperationException("INPUT_FAILED");
    }

    public static void TypeText(AutomationElement element, string text, Action validate)
    {
        if (text.Length == 0 || text.Length > 128 || text.Any(char.IsControl)) throw new InvalidOperationException("INPUT_TEXT_UNSUPPORTED");
        if (element.Current.ControlType != ControlType.Edit || !element.Current.HasKeyboardFocus)
            throw new InvalidOperationException("TEXT_TARGET_NOT_FOCUSED");
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern) && ((ValuePattern)pattern).Current.IsReadOnly)
            throw new InvalidOperationException("READ_ONLY");
        var inputs = text.SelectMany(unit => new[] { Key(0, unit, 4), Key(0, unit, 6) }).ToArray();
        Send(inputs, text.Select(unit => Key(0, unit, 6)).ToArray(), () =>
        {
            validate();
            if (!element.Current.HasKeyboardFocus) throw new InvalidOperationException("FOCUS_CHANGED");
        });
    }

    public static void Drag(IntPtr window, int fromX, int fromY, int toX, int toY, Action validate)
    {
        EnsureIdle();
        if (fromX < 0 || fromY < 0 || toX < 0 || toY < 0) throw new InvalidOperationException("POINT_OUTSIDE_DESKTOP");
        validate();
        if (!SetCursorPos(fromX, fromY)) throw new InvalidOperationException("INPUT_FAILED");
        if (SendInput(1, new[] { MouseEvent(2) }, Marshal.SizeOf<Input>()) != 1) throw new InvalidOperationException("UNKNOWN_EFFECT");
        try
        {
            var steps = Math.Clamp((int)Math.Sqrt(Math.Pow(toX - fromX, 2) + Math.Pow(toY - fromY, 2)) / 12, 2, 40);
            for (var i = 1; i <= steps; i++)
            {
                validate();
                if (!SetCursorPos(fromX + (toX - fromX) * i / steps, fromY + (toY - fromY) * i / steps)) throw new InvalidOperationException("UNKNOWN_EFFECT");
            }
        }
        finally
        {
            // Never leave a synthetic button held if cancellation/focus loss occurs.
            SendInput(1, new[] { MouseEvent(4) }, Marshal.SizeOf<Input>());
        }
    }

    public static void TypeDocument(AutomationElement element, string text, Action validate)
    {
        if (text.Any(char.IsControl)) throw new InvalidOperationException("USE_REVIEWED_KEY_STEPS");
        if (element.Current.ControlType != ControlType.Edit && element.Current.ControlType != ControlType.Document)
            throw new InvalidOperationException("TEXT_TARGET_NOT_EDITABLE");
        Unicode(text, () =>
        {
            validate();
            if (element.Current.IsPassword || !element.Current.HasKeyboardFocus) throw new InvalidOperationException("FOCUS_CHANGED");
        });
    }

    public static void PressKey(string key, Action validate)
    {
        if (string.IsNullOrWhiteSpace(key) || key.Length > 32) throw new InvalidOperationException("KEY_UNSUPPORTED");
        var parts = key.Split('+');
        if (parts[..^1].Any(p => p.ToUpperInvariant() is not ("CTRL" or "SHIFT" or "ALT" or "WIN")))
            throw new InvalidOperationException("KEY_UNSUPPORTED");
        Chord(parts, validate);
    }
}
