using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Automation;

// One disposable MTA worker per request bounds a hung third-party UIA provider.
internal static partial class Program
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private delegate bool EnumProc(IntPtr hwnd, IntPtr param);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr param);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr hwnd, EnumProc callback, IntPtr param);
    [DllImport("user32.dll")] private static extern bool IsChild(IntPtr parent, IntPtr child);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr hwnd, uint attribute, out int value, uint size);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool GetUserObjectInformation(IntPtr handle, int kind, StringBuilder value, uint bytes, out uint needed);
    [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode)] private static extern bool WTSQuerySessionInformation(IntPtr server, int session, int kind, out IntPtr value, out uint bytes);
    [DllImport("wtsapi32.dll")] private static extern void WTSFreeMemory(IntPtr value);
    [DllImport("advapi32.dll")] private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll")] private static extern bool GetTokenInformation(IntPtr token, int kind, out int value, uint bytes, out uint needed);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

    private static void Interactive()
    {
        if (!Environment.UserInteractive || !WTSQuerySessionInformation(IntPtr.Zero, -1, 8, out var state, out var size)) throw new InvalidOperationException("SESSION_UNAVAILABLE");
        try { if (size < 4 || Marshal.ReadInt32(state) != 0) throw new InvalidOperationException("SESSION_DISCONNECTED"); } finally { WTSFreeMemory(state); }
        var desktop = OpenInputDesktop(0, false, 1);
        if (desktop == IntPtr.Zero) throw new InvalidOperationException("SECURE_DESKTOP");
        try
        {
            var name = new StringBuilder(256);
            if (!GetUserObjectInformation(desktop, 2, name, 512, out _) || name.ToString() != "Default") throw new InvalidOperationException("SECURE_DESKTOP");
        }
        finally { CloseDesktop(desktop); }
    }

    private static void StandardUser(Process process)
    {
        var handle = OpenProcess(0x1000, false, (uint)process.Id);
        if (handle == IntPtr.Zero) throw new InvalidOperationException("TARGET_ACCESS_DENIED");
        try
        {
            if (!OpenProcessToken(handle, 8, out var token)) throw new InvalidOperationException("TARGET_ACCESS_DENIED");
            try { if (!GetTokenInformation(token, 20, out var elevated, 4, out _) || elevated != 0) throw new InvalidOperationException("ELEVATED_TARGET_UNSUPPORTED"); }
            finally { CloseHandle(token); }
        }
        finally { CloseHandle(handle); }
    }

    [MTAThread]
    private static void Main()
    {
        try
        {
            SetProcessDpiAwarenessContext(new IntPtr(-4)); // UIA and input use physical screen pixels.
            var line = Console.ReadLine();
            if (line is null || line.Length > 4194304) throw new InvalidOperationException("INVALID_REQUEST");
            var request = JsonNode.Parse(line)!.AsObject();
            Interactive();
            if (request["op"]?.GetValue<string>() == "record") { Record(request); return; }
            object result = request["op"]!.GetValue<string>() switch
            {
                "health" or "capabilities" => new { protocol = 1, state = "ready", platform = "windows", interactive = Environment.UserInteractive,
                    graphical_actions = new[] { "screenshot", "click", "double_click", "drag", "draw", "move", "scroll", "keypress", "type", "wait" },
                    coordinate_space = "screenshot", scroll_units = "wheel_delta", max_path_points = 600, max_strokes = 40, max_wait_ms = 2000 },
                "readiness" => Readiness(),
                "targets" => Targets(),
                "applications" => Applications.Catalog(),
                "launch" => Applications.Launch(request),
                "window" => WindowIdentity(request),
                "observe" => Observe(request),
                "snapshot" => Snapshot(request),
                "capture" => Capture(request),
                "state" or "visual_state" => State(request),
                "visual_action" => GraphicalActions.Execute(request, () => Target(request), () => { Interactive(); var (h, _) = Target(request); if (!TargetForeground(h)) throw new InvalidOperationException("FOREGROUND_CHANGED"); }, () => State(request)),
                "wait" => Wait(request),
                "action" => Act(request),
                _ => throw new InvalidOperationException("UNSUPPORTED_OPERATION")
            };
            Console.WriteLine(JsonSerializer.Serialize(new { ok = true, result }, Json));
        }
        catch (Exception ex)
        {
            var code = ex is InvalidOperationException ? ex.Message : "UIA_UNAVAILABLE";
            // The type says what failed (for example COMException or ElementNotAvailableException)
            // without exposing application content.
            var detail = ex is InvalidOperationException ? ex.Data["reason"] as string ?? "" : ex.GetType().Name;
            Console.WriteLine(JsonSerializer.Serialize(new { ok = false, error = code, detail }, Json));
        }
    }

    private static object Targets()
    {
        var windows = new List<object>();
        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd) || windows.Count >= 100 || IsCloaked(hwnd)) return true;
            var title = new StringBuilder(512);
            GetWindowText(hwnd, title, title.Capacity);
            if (title.Length == 0) return true;
            GetWindowThreadProcessId(hwnd, out var pid);
            try
            {
                using var process = Process.GetProcessById((int)pid);
                if (process.SessionId != Process.GetCurrentProcess().SessionId) return true;
                var name = process.ProcessName;
                if (IsOwnInterface(name, title.ToString())) return true;
                long? hostedHwnd = null;
                uint? hostedPid = null;
                string? hostedCreated = null;
                if (name.Equals("ApplicationFrameHost", StringComparison.OrdinalIgnoreCase))
                {
                    EnumChildWindows(hwnd, (child, _) =>
                    {
                        GetWindowThreadProcessId(child, out var childPid);
                        if (childPid == pid || childPid == 0) return true;
                        try
                        {
                            using var hosted = Process.GetProcessById((int)childPid);
                            if (hosted.SessionId != process.SessionId) return true;
                            hostedHwnd = child.ToInt64(); hostedPid = childPid;
                            hostedCreated = hosted.StartTime.ToUniversalTime().Ticks.ToString();
                            name = hosted.ProcessName;
                            return false;
                        }
                        catch (Exception) { return true; }
                    }, IntPtr.Zero);
                    if (hostedPid is null) return true;
                }
                windows.Add(new { hwnd = hwnd.ToInt64(), pid, created = process.StartTime.ToUniversalTime().Ticks.ToString(), label = title.ToString(), application = name, hosted_hwnd = hostedHwnd, hosted_pid = hostedPid, hosted_created = hostedCreated });
            }
            catch (Exception) { /* A vanished/inaccessible window is not a target. */ }
            return true;
        }, IntPtr.Zero);
        return new { targets = windows };
    }

    // PANINI never targets its own avatar or native helpers (that would be self-control). Every other
    // window is a valid target, including PANINI-CLI, terminals and other Electron applications.
    private static bool IsOwnInterface(string process, string title)
    {
        if (process.StartsWith("Panini.", StringComparison.OrdinalIgnoreCase) && !process.Equals("Panini.Desktop.Fixture", StringComparison.OrdinalIgnoreCase)) return true;
        var avatar = process.Equals("electron", StringComparison.OrdinalIgnoreCase) || process.Equals("PANINI", StringComparison.OrdinalIgnoreCase);
        return avatar && title.StartsWith("PANINI", StringComparison.OrdinalIgnoreCase);
    }

    private static object Readiness()
    {
        try
        {
            Interactive();
            return new { ready = true, interactive = true, input_desktop = "Default", session = Environment.ProcessId > 0 ? Process.GetCurrentProcess().SessionId : -1, recovery = "Unlock the desktop and select the target window." };
        }
        catch (Exception ex)
        {
            return new { ready = false, interactive = false, input_desktop = "unknown", session = -1, reason = ex.Message, recovery = "Unlock the Windows desktop and return to the Default input desktop." };
        }
    }

    private static bool IsCloaked(IntPtr hwnd) => DwmGetWindowAttribute(hwnd, 14, out var value, 4) == 0 && value != 0;
    private static bool TargetForeground(IntPtr hwnd)
    {
        var foreground = GetForegroundWindow();
        return foreground == hwnd || IsChild(hwnd, foreground);
    }

    private static IntPtr ValidatedWindow(JsonObject request)
    {
        var target = request["target"]!;
        var hwnd = new IntPtr(target["hwnd"]!.GetValue<long>());
        GetWindowThreadProcessId(hwnd, out var pid);
        if (pid != target["pid"]!.GetValue<uint>() || !IsWindowVisible(hwnd) || IsCloaked(hwnd)) throw new InvalidOperationException("TARGET_CHANGED");
        using var process = Process.GetProcessById((int)pid);
        StandardUser(process);
        if (process.StartTime.ToUniversalTime().Ticks.ToString() != target["created"]!.GetValue<string>()) throw new InvalidOperationException("TARGET_CHANGED");
        if (process.SessionId != Process.GetCurrentProcess().SessionId) throw new InvalidOperationException("SESSION_CHANGED");
        if (target["hosted_pid"] is not null)
        {
            var child = new IntPtr(target["hosted_hwnd"]!.GetValue<long>());
            GetWindowThreadProcessId(child, out var hostedPid);
            if (!IsChild(hwnd, child) || hostedPid != target["hosted_pid"]!.GetValue<uint>()) throw new InvalidOperationException("TARGET_CHANGED");
            using var hosted = Process.GetProcessById((int)hostedPid);
            StandardUser(hosted);
            if (hosted.SessionId != process.SessionId || hosted.StartTime.ToUniversalTime().Ticks.ToString() != target["hosted_created"]!.GetValue<string>()) throw new InvalidOperationException("TARGET_CHANGED");
        }
        return hwnd;
    }

    private static object WindowIdentity(JsonObject request)
    {
        var hwnd = ValidatedWindow(request);
        var title = new StringBuilder(512);
        GetWindowText(hwnd, title, title.Capacity);
        GetWindowThreadProcessId(hwnd, out var pid);
        using var process = Process.GetProcessById((int)pid);
        return new { hwnd = hwnd.ToInt64(), pid, created = process.StartTime.ToUniversalTime().Ticks.ToString(), title = title.ToString(), application = process.ProcessName, visible = true };
    }

    private static (IntPtr hwnd, AutomationElement root) Target(JsonObject request)
    {
        var hwnd = ValidatedWindow(request);
        return (hwnd, AutomationElement.FromHandle(hwnd));
    }

    private static object Capture(JsonObject request)
    {
        var (hwnd, _) = Target(request);
        var result = WindowCapture.Capture(hwnd);
        Target(request);
        return result;
    }

    private static object State(JsonObject request)
    {
        // Sequential UIA and image observations; never claim atomic UI state.
        var observation = Observe(request);
        var capture = Capture(request);
        return new { observation, capture, snapshot_kind = "uia_plus_window_png" };
    }

    private static object Wait(JsonObject request)
    {
        var milliseconds = request["milliseconds"]?.GetValue<int>() ?? 250;
        if (milliseconds is < 0 or > 2000) throw new InvalidOperationException("WAIT_OUT_OF_RANGE");
        Thread.Sleep(milliseconds);
        return new { waited_ms = milliseconds };
    }

    private static bool VisibleWithinAncestors(AutomationElement element, AutomationElement root)
    {
        var bounds = element.Current.BoundingRectangle;
        if (bounds.IsEmpty || bounds.Width <= 0 || bounds.Height <= 0) return false;
        // WPF providers can report clipped descendants as IsOffscreen=false.
        // Intersect scroll viewports as well as the selected window bounds.
        bounds.Intersect(root.Current.BoundingRectangle);
        var parent = TreeWalker.ControlViewWalker.GetParent(element);
        for (var depth = 0; parent is not null && depth < 64; depth++)
        {
            if (parent.TryGetCurrentPattern(ScrollPattern.Pattern, out _))
                bounds.Intersect(parent.Current.BoundingRectangle);
            if (bounds.IsEmpty || bounds.Width <= 0 || bounds.Height <= 0) return false;
            if (Automation.Compare(parent, root)) return true;
            parent = TreeWalker.ControlViewWalker.GetParent(parent);
        }
        return false;
    }

    private static object Observe(JsonObject request)
    {
        var (hwnd, root) = Target(request);
        var rows = new List<object>();
        var elements = root.FindAll(TreeScope.Descendants, Condition.TrueCondition);
        for (var i = 0; i < Math.Min(elements.Count, 500); i++)
        {
            var e = elements[i];
            var c = e.Current;
            if (c.IsOffscreen || c.IsPassword || !VisibleWithinAncestors(e, root)) continue;
            string? value = null;
            if (e.TryGetCurrentPattern(ValuePattern.Pattern, out var p)) value = ((ValuePattern)p).Current.Value;
            else if (e.TryGetCurrentPattern(TogglePattern.Pattern, out p)) value = ((TogglePattern)p).Current.ToggleState.ToString().ToLowerInvariant();
            else if (e.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p)) value = ((SelectionItemPattern)p).Current.IsSelected ? "selected" : "not_selected";
            var bounds = c.BoundingRectangle;
            rows.Add(new { element_id = string.Join('.', e.GetRuntimeId()), name = c.Name[..Math.Min(c.Name.Length, 500)], automation_id = c.AutomationId, type = c.ControlType.ProgrammaticName, enabled = c.IsEnabled, value = value?[..Math.Min(value.Length, 2000)], bounds = new { x = bounds.X, y = bounds.Y, width = bounds.Width, height = bounds.Height }, patterns = e.GetSupportedPatterns().Select(pat => pat.ProgrammaticName).ToArray() });
        }
        var focused = AutomationElement.FocusedElement;
        var focusedId = focused is not null && !focused.Current.IsPassword ? string.Join('.', focused.GetRuntimeId()) : null;
        return new { observation_id = Guid.NewGuid().ToString(), observed_at = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), foreground = TargetForeground(hwnd), focused_element_id = focusedId, title = root.Current.Name, elements = rows };
    }

    private static object Act(JsonObject request)
    {
        var (hwnd, root) = Target(request);
        var action = request["action"]!;
        var kind = action["kind"]!.GetValue<string>();
        if (kind == "focus" && action["element_id"] is null && action["expected_type"] is null)
        {
            SetForegroundWindow(hwnd);
            return new { dispatched = true, verified = TargetForeground(hwnd), effect = "foreground" };
        }
        if (kind != "activate" && !TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED");
        if (request["expected_window_title"] is not null && root.Current.Name != request["expected_window_title"]!.GetValue<string>())
            throw new InvalidOperationException("WINDOW_CHANGED");
        var observed = request["observed_at"]!.GetValue<long>();
        var age = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - observed;
        if (age > 5000 || age < -100) throw new InvalidOperationException("OBSERVATION_EXPIRED");
        var id = action["element_id"]!.GetValue<string>();
        var matches = action["expected_type"] is not null ? FineTargets(root, id) : root.FindAll(TreeScope.Descendants, Condition.TrueCondition).Cast<AutomationElement>().Where(e => string.Join('.', e.GetRuntimeId()) == id).Take(2).ToArray();
        if (matches.Length != 1) throw new InvalidOperationException("ELEMENT_CHANGED");
        var element = matches[0];
        var current = element.Current;
        if (current.IsPassword || !current.IsEnabled || current.IsOffscreen
            || !(kind == "activate" && Automation.Compare(element, root)) && !VisibleWithinAncestors(element, root)) throw new InvalidOperationException("ELEMENT_UNAVAILABLE");
        if (current.Name != action["expected_name"]!.GetValue<string>() || current.AutomationId != action["expected_automation_id"]!.GetValue<string>()) throw ChangedElement("label");
        if (action["expected_type"] is not null)
        {
            var b = current.BoundingRectangle;
            var expected = action["expected_bounds"]!;
            if (Credential(current.Name, current.AutomationId) || current.ControlType.ProgrammaticName != action["expected_type"]!.GetValue<string>()
                || b.X != expected["x"]!.GetValue<double>() || b.Y != expected["y"]!.GetValue<double>()
                || b.Width != expected["width"]!.GetValue<double>() || b.Height != expected["height"]!.GetValue<double>())
                throw ChangedElement("type_or_bounds");
            ValidateFineState(element, action);
            if (kind == "keypress" && !Automation.Compare(AutomationElement.FocusedElement, element))
                throw new InvalidOperationException("FOCUS_CHANGED");
        }
        // Check identity/focus immediately before dispatch, including after potentially slow UIA calls.
        Target(request);
        if (kind != "activate" && !TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED");
        if (request["expected_window_title"] is not null && root.Current.Name != request["expected_window_title"]!.GetValue<string>())
            throw new InvalidOperationException("WINDOW_CHANGED");
        bool verified = false;
        switch (kind)
        {
            case "activate":
                if (action["expected_type"] is null || !Automation.Compare(element, root))
                    throw new InvalidOperationException("WINDOW_ROOT_REQUIRED");
                ActivateWindow(request, hwnd);
                verified = true;
                break;
            case "focus":
                if (action["expected_type"] is null) throw new InvalidOperationException("SNAPSHOT_REQUIRED");
                element.SetFocus();
                Target(request);
                if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED");
                verified = Automation.Compare(AutomationElement.FocusedElement, element);
                if (!verified) throw new InvalidOperationException("FOCUS_CHANGED");
                break;
            case "click":
                GroundedInput.Click(hwnd, element, () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); }, 1,
                    action["expected_type"] is not null ? (x, y) => ValidateBoundsHit(root, element, x, y) : null);
                break;
            case "double_click":
                GroundedInput.Click(hwnd, element, () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); }, 2,
                    action["expected_type"] is not null ? (x, y) => ValidateBoundsHit(root, element, x, y) : null);
                break;
            case "move":
                GroundedInput.Move(action["x"]!.GetValue<int>(), action["y"]!.GetValue<int>(), () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); });
                break;
            case "type_text":
                GroundedInput.TypeText(element, action["text"]!.GetValue<string>(), () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); });
                break;
            case "fine_type":
                if (action["expected_type"] is null) throw new InvalidOperationException("SNAPSHOT_REQUIRED");
                verified = GroundedInput.TypeDocument(element, action["text"]!.GetValue<string>(), () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); }, action["replace"]?.GetValue<bool>() ?? false);
                break;
            case "keypress":
                GroundedInput.PressKey(action["key"]!.GetValue<string>(), () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); });
                break;
            case "drag":
                GroundedInput.Drag(hwnd, action["from_x"]!.GetValue<int>(), action["from_y"]!.GetValue<int>(), action["to_x"]!.GetValue<int>(), action["to_y"]!.GetValue<int>(), () => { Target(request); if (!TargetForeground(hwnd)) throw new InvalidOperationException("FOREGROUND_CHANGED"); });
                break;
            case "set_value":
                var text = action["text"]!.GetValue<string>();
                if (text.Length > 4000) throw new InvalidOperationException("VALUE_TOO_LARGE");
                var value = (ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern);
                if (value.Current.IsReadOnly) throw new InvalidOperationException("READ_ONLY");
                value.SetValue(text);
                verified = value.Current.Value == text;
                break;
            case "invoke": ((InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern)).Invoke(); break;
            case "select":
                var selection = (SelectionItemPattern)element.GetCurrentPattern(SelectionItemPattern.Pattern);
                selection.Select(); verified = selection.Current.IsSelected; break;
            case "toggle":
                var toggle = (TogglePattern)element.GetCurrentPattern(TogglePattern.Pattern);
                var before = toggle.Current.ToggleState;
                toggle.Toggle(); verified = before != toggle.Current.ToggleState; break;
            case "scroll":
                var scroll = (ScrollPattern)element.GetCurrentPattern(ScrollPattern.Pattern);
                scroll.Scroll(ScrollAmount.NoAmount, action["direction"]?.GetValue<string>() == "up" ? ScrollAmount.LargeDecrement : ScrollAmount.LargeIncrement); break;
            default: throw new InvalidOperationException("UNSUPPORTED_ACTION");
        }
        return new { dispatched = true, verified, effect = kind, evidence_id = Guid.NewGuid().ToString() };
    }
}
