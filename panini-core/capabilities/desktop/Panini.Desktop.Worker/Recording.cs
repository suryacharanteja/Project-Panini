using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Automation;

internal static partial class Program
{
    private delegate IntPtr RecordingHook(int code, IntPtr message, IntPtr data);
    [DllImport("user32.dll")] private static extern IntPtr SetWindowsHookEx(int id, RecordingHook callback, IntPtr module, uint thread);
    [DllImport("user32.dll")] private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")] private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr message, IntPtr data);
    [DllImport("user32.dll")] private static extern int GetMessage(out RecordingMessage message, IntPtr window, uint min, uint max);
    [DllImport("user32.dll")] private static extern bool TranslateMessage(ref RecordingMessage message);
    [DllImport("user32.dll")] private static extern IntPtr DispatchMessage(ref RecordingMessage message);
    [DllImport("user32.dll")] private static extern bool PostThreadMessage(uint thread, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr GetModuleHandle(string? name);
    [StructLayout(LayoutKind.Sequential)] private struct RecordingPoint { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] private struct RecordingMessage { public IntPtr Window; public uint Message; public UIntPtr W; public IntPtr L; public uint Time; public RecordingPoint Point; public uint Private; }
    [StructLayout(LayoutKind.Sequential)] private struct RecordingMouse { public RecordingPoint Point; public uint Data, Flags, Time; public UIntPtr Extra; }
    // Events never contain printable key codes or raw keyboard text.
    private sealed record RecordingEvent(string Kind, int X = 0, int Y = 0, string Key = "");

    private static JsonObject RecordingSnapshot(JsonObject request)
    {
        var snapshot = JsonSerializer.SerializeToNode(Snapshot(request), Json)!.AsObject();
        snapshot.Remove("focused_element_id");
        var index = 0;
        foreach (var node in snapshot["nodes"]!.AsArray())
        {
            var row = node!.AsObject();
            row.Remove("element_id"); row.Remove("automation_id");
            row["ref"] = "w" + ++index;
        }
        return snapshot;
    }

    private static void Record(JsonObject request)
    {
        var (hwnd, root) = Target(request);
        if (!TargetForeground(hwnd)) ActivateWindow(request, hwnd);
        if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED");
        using var owner = Process.GetProcessById(request["owner_pid"]!.GetValue<int>());
        if (owner.StartTime.ToUniversalTime().Ticks.ToString() != request["owner_created"]!.GetValue<string>()) throw new InvalidOperationException("RECORDING_OWNER_CHANGED");
        var before = RecordingSnapshot(request);
        using var cancel = new CancellationTokenSource(TimeSpan.FromMinutes(15));
        var recordingEnded = 0;
        // Provider calls can hang. Broker death must terminate hooks even while UIA is blocked.
        _ = Task.Run(async () =>
        {
            while (Volatile.Read(ref recordingEnded) == 0)
            {
                await Task.Delay(250);
                try { if (owner.HasExited) Environment.Exit(1); }
                catch (InvalidOperationException) { return; }
            }
        });
        using var queue = new BlockingCollection<RecordingEvent>(128);
        using var ready = new ManualResetEventSlim();
        var events = new JsonArray();
        string? hookFailure = null;
        uint hookThread = 0;
        var overflow = 0;
        var textQueued = 0;
        var pump = new Thread(() =>
        {
            hookThread = GetCurrentThreadId();
            void Add(RecordingEvent item)
            {
                if (item.Kind == "text") { if (Interlocked.Exchange(ref textQueued, 1) != 0) return; }
                else Interlocked.Exchange(ref textQueued, 0);
                if (!queue.TryAdd(item)) Interlocked.Exchange(ref overflow, 1);
            }
            bool Scoped() => TargetForeground(hwnd);
            RecordingHook mouse = (code, message, data) =>
            {
                if (code >= 0 && Scoped())
                {
                    var id = message.ToInt32();
                    if (id == 0x201)
                    {
                        var down = Marshal.PtrToStructure<RecordingMouse>(data);
                        Add(new("down", down.Point.X, down.Point.Y)); // A press: a drag if the release is elsewhere.
                    }
                    else if (id is 0x202 or 0x205 or 0x20a)
                    {
                        var value = Marshal.PtrToStructure<RecordingMouse>(data);
                        Add(new(id == 0x202 ? "click" : id == 0x205 ? "manual" : ((short)(value.Data >> 16) > 0 ? "scroll_up" : "scroll_down"), value.Point.X, value.Point.Y));
                    }
                }
                return CallNextHookEx(IntPtr.Zero, code, message, data);
            };
            RecordingHook keyboard = (code, message, data) =>
            {
                if (code >= 0 && Scoped() && message.ToInt32() is 0x100 or 0x104)
                {
                    var key = Marshal.ReadInt32(data);
                    bool Held(int vk) => GetAsyncKeyState(vk) < 0;
                    var control = Held(0x11); var alt = Held(0x12); var shift = Held(0x10); var win = Held(0x5b) || Held(0x5c);
                    string? named = key switch { 13 => "Enter", 9 => "Tab", 27 => "Escape", 46 => "Delete", 37 => "ArrowLeft", 38 => "ArrowUp", 39 => "ArrowRight", 40 => "ArrowDown", 36 => "Home", 35 => "End", 33 => "PageUp", 34 => "PageDown", _ => null };
                    if (control || alt || win)
                    {
                        named ??= key is >= 65 and <= 90 ? ((char)key).ToString() : null;
                        if (named is not null) Add(new("key", Key: (control ? "Ctrl+" : "") + (alt ? "Alt+" : "") + (shift ? "Shift+" : "") + (win ? "Win+" : "") + named));
                        else if (key is not (16 or 17 or 18 or 91 or 92)) Add(new("manual"));
                    }
                    else if (named is not null) Add(new("key", Key: (shift ? "Shift+" : "") + named));
                    else if (key is not (16 or 17 or 18 or 91 or 92)) Add(new("text"));
                }
                return CallNextHookEx(IntPtr.Zero, code, message, data);
            };
            var mouseHook = SetWindowsHookEx(14, mouse, GetModuleHandle(null), 0);
            var keyHook = SetWindowsHookEx(13, keyboard, GetModuleHandle(null), 0);
            try
            {
                if (mouseHook == IntPtr.Zero || keyHook == IntPtr.Zero) { hookFailure = "RECORDING_HOOK_UNAVAILABLE"; return; }
                ready.Set();
                while (!cancel.IsCancellationRequested && GetMessage(out var msg, IntPtr.Zero, 0, 0) > 0) { TranslateMessage(ref msg); DispatchMessage(ref msg); }
            }
            finally { if (mouseHook != IntPtr.Zero) UnhookWindowsHookEx(mouseHook); if (keyHook != IntPtr.Zero) UnhookWindowsHookEx(keyHook); ready.Set(); GC.KeepAlive(mouse); GC.KeepAlive(keyboard); }
        }) { IsBackground = true, Name = "PANINI owner recording hooks" };
        pump.SetApartmentState(ApartmentState.STA);
        pump.Start();
        if (!ready.Wait(3000) || hookFailure is not null) throw new InvalidOperationException(hookFailure ?? "RECORDING_HOOK_TIMEOUT");
        Console.WriteLine(JsonSerializer.Serialize(new { ok = true, result = new { state = "recording", before } }, Json));
        Console.Out.Flush();
        _ = Task.Run(() => { Console.ReadLine(); cancel.Cancel(); });
        AutomationElement? pendingField = null;
        (int X, int Y)? pressed = null;
        JsonObject? pendingTarget = null;
        string? lastValue = null;
        string? failure = null;
        bool Belongs(AutomationElement element)
        {
            var walker = TreeWalker.ControlViewWalker;
            for (var depth = 0; element is not null && depth <= 30; depth++)
            {
                var c = element.Current;
                if (c.IsPassword || Credential(c.Name, c.AutomationId)) return false;
                if (Automation.Compare(element, root)) return true;
                element = walker.GetParent(element)!;
            }
            return false;
        }
        JsonObject? Selector(AutomationElement? element)
        {
            if (element is null || !Belongs(element)) return null;
            var c = element.Current;
            if (!c.IsEnabled || c.IsOffscreen || string.IsNullOrEmpty(c.Name)) return null;
            var ancestry = new JsonArray();
            var parent = TreeWalker.ControlViewWalker.GetParent(element);
            for (var i = 0; i < 30 && ancestry.Count < 8 && parent is not null && !Automation.Compare(parent, root); i++)
            {
                var p = parent.Current;
                if (p.Name.Length > 0) ancestry.Insert(0, new JsonObject { ["type"] = p.ControlType.ProgrammaticName, ["name"] = p.Name[..Math.Min(p.Name.Length, 500)] });
                parent = TreeWalker.ControlViewWalker.GetParent(parent);
            }
            return new JsonObject { ["type"] = c.ControlType.ProgrammaticName, ["name"] = c.Name[..Math.Min(c.Name.Length, 500)], ["ancestors"] = ancestry };
        }
        void Append(string kind, JsonObject? target, string? value = null, string? crop = null, JsonObject? drop = null)
        {
            if (events.Count >= 100) throw new InvalidOperationException("RECORDING_STEP_LIMIT");
            if (target is null || (kind == "drag" && drop is null)) { kind = "manual"; value = null; crop = null; drop = null; }
            var entry = new JsonObject { ["kind"] = kind, ["target"] = target?.DeepClone(), ["value"] = value };
            if (crop is not null) entry["crop"] = crop; // Only for grounded, non-credential targets.
            if (drop is not null) entry["drop"] = drop.DeepClone();
            events.Add(entry);
        }
        string? FieldValue(AutomationElement field)
        {
            if (!Belongs(field)) return null;
            string? value = null;
            if (field.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern)) value = ((ValuePattern)pattern).Current.Value;
            else if (field.TryGetCurrentPattern(TextPattern.Pattern, out pattern)) value = ((TextPattern)pattern).DocumentRange.GetText(2001);
            if (!Belongs(field) || value?.Length > 2000) return null;
            return value;
        }
        void Flush()
        {
            if (pendingField is not null)
            {
                var value = FieldValue(pendingField);
                if (value is not null && value != lastValue) Append("type", pendingTarget, value);
                else if (value is null) Append("manual", null);
            }
            pendingField = null; pendingTarget = null; lastValue = null;
        }
        AutomationElement? At(int x, int y)
        {
            // WinUI FromPoint is unreliable. Search bounded ControlView geometry instead.
            AutomationElement? found = null;
            var deepest = -1; var visited = 0;
            var stack = new Stack<(AutomationElement Element, int Depth)>(); stack.Push((root, 0));
            while (stack.Count > 0)
            {
                var (element, depth) = stack.Pop();
                if (++visited > 2400 || depth > 30) return null;
                var c = element.Current;
                if (c.IsOffscreen || !c.BoundingRectangle.Contains(x, y)) continue;
                if (c.IsPassword || Credential(c.Name, c.AutomationId)) return null;
                if (depth > deepest && c.Name.Length > 0) { found = element; deepest = depth; }
                var child = TreeWalker.ControlViewWalker.GetFirstChild(element);
                while (child is not null) { if (stack.Count >= 2400) return null; stack.Push((child, depth + 1)); child = TreeWalker.ControlViewWalker.GetNextSibling(child); }
            }
            return found;
        }
        try
        {
            while (!cancel.IsCancellationRequested)
            {
                if (owner.HasExited) { failure = "RECORDING_OWNER_ENDED"; break; }
                Interactive(); Target(request);
                if (overflow != 0) throw new InvalidOperationException("RECORDING_EVENT_LIMIT");
                if (!queue.TryTake(out var item, 100)) continue;
                if (!TargetForeground(hwnd)) { Flush(); continue; }
                if (item.Kind == "down") { pressed = (item.X, item.Y); continue; }
                if (item.Kind == "click" && pressed is { } start
                    && Math.Abs(item.X - start.X) + Math.Abs(item.Y - start.Y) > 12)
                {
                    // Press here, release there: a drag from one named control to another.
                    pressed = null;
                    Flush();
                    Append("drag", Selector(At(start.X, start.Y)), drop: Selector(At(item.X, item.Y)));
                    continue;
                }
                if (item.Kind == "click") pressed = null;
                if (item.Kind == "text")
                {
                    Interlocked.Exchange(ref textQueued, 0);
                    var field = AutomationElement.FocusedElement;
                    var selector = Selector(field);
                    if (selector is null) { Flush(); continue; }
                    if (pendingField is not null && !Automation.Compare(pendingField, field)) Flush();
                    if (pendingField is null)
                    {
                        pendingField = field; pendingTarget = selector;
                        // Capture the final UIA value, rather than logging printable key codes.
                        lastValue = null;
                    }
                }
                else
                {
                    // A text aggregate preserves the pre-key control even when Tab/Enter moves focus.
                    var keyboardField = pendingField;
                    Flush();
                    var element = item.Kind == "key" ? keyboardField ?? AutomationElement.FocusedElement : At(item.X, item.Y);
                    if (item.Kind == "key" && keyboardField is null && item.Key.Split('+')[^1] is not ("ArrowLeft" or "ArrowRight" or "ArrowUp" or "ArrowDown" or "Home" or "End" or "PageUp" or "PageDown"))
                    {
                        Append("manual", null); // Post-key focus alone cannot identify the original receiver.
                        continue;
                    }
                    var selector = Selector(element);
                    var crop = item.Kind == "click" && selector is not null ? WindowCapture.CropWithin(hwnd, item.X, item.Y, 500) : null;
                    Append(item.Kind, selector, item.Kind == "key" ? item.Key : null, crop);
                }
            }
            // UIA processing may be behind the hook queue; replayability needs a complete sequence.
            if (queue.Count > 0) throw new InvalidOperationException("RECORDING_EVENTS_PENDING");
            Flush();
        }
        catch (Exception ex) { failure = ex is InvalidOperationException ? ex.Message : "RECORDING_TARGET_CHANGED"; }
        finally { cancel.Cancel(); PostThreadMessage(hookThread, 0x12, IntPtr.Zero, IntPtr.Zero); pump.Join(1000); }
        var after = failure is null ? RecordingSnapshot(request) : null;
        Console.WriteLine(JsonSerializer.Serialize(new { ok = true, result = new { state = failure is null ? "stopped" : "interrupted", events, after, failure } }, Json));
        Volatile.Write(ref recordingEnded, 1);
    }
}
