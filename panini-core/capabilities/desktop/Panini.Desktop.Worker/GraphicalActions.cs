using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;

// Screenshot pixels are mapped to physical pixels only by the executor.
// The broker supplies frame metadata from its own one-use observation receipt.
internal static class GraphicalActions
{
    private const int MaxStrokes = 40;
    private const int MaxStrokePoints = 600;
    private const int MaxTotalPoints = 4000;

    [StructLayout(LayoutKind.Sequential)] private struct NativePoint { public int X, Y; }
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(NativePoint point);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);

    // The broker's cursor overlay reads these progress lines; they are never part of the result.
    private static void Progress(string label, int index, int total)
    {
        try { Console.Error.WriteLine(JsonSerializer.Serialize(new { progress = new { label, index, total } })); Console.Error.Flush(); }
        catch (Exception) { /* Progress is cosmetic. */ }
    }

    public static object Execute(JsonObject request, Func<(IntPtr hwnd, AutomationElement root)> target, Action validate, Func<object> state)
    {
        var action = request["action"]!.AsObject();
        var kind = action["kind"]!.GetValue<string>();
        if (kind == "screenshot") return new { dispatched = false, verified = false, after = state() };
        if (kind == "wait")
        {
            var ms = action["milliseconds"]?.GetValue<int>() ?? 250;
            if (ms is < 0 or > 2000) throw new InvalidOperationException("WAIT_OUT_OF_RANGE");
            for (var elapsed = 0; elapsed < ms; elapsed += 50) { target(); Thread.Sleep(Math.Min(50, ms - elapsed)); }
            return new { dispatched = false, verified = false, waited_ms = ms, after = state() };
        }
        validate();
        var (hwnd, root) = target();
        var frame = request["frame"] ?? throw new InvalidOperationException("SNAPSHOT_REQUIRED");
        var original = (frame["source_x"]!.GetValue<int>(), frame["source_y"]!.GetValue<int>(), frame["source_width"]!.GetValue<int>(), frame["source_height"]!.GetValue<int>());
        void Check()
        {
            validate();
            if (WindowCapture.Bounds(hwnd) != original) throw new InvalidOperationException("WINDOW_GEOMETRY_CHANGED");
        }
        Check();
        var points = new List<(int X, int Y)>();
        var strokes = new List<List<(int X, int Y)>>();
        var elementId = action["element_id"]?.GetValue<string>() ?? "";
        if (elementId.Length > 0 && kind is not ("click" or "double_click"))
            throw new InvalidOperationException("ELEMENT_ANCHOR_UNSUPPORTED");
        var anchor = elementId.Length > 0 ? new ElementAnchor(elementId, frame, root) : null;
        (int X, int Y) Map(JsonNode point)
        {
            var x = point["x"]!.GetValue<int>(); var y = point["y"]!.GetValue<int>();
            var width = frame["width"]!.GetValue<int>(); var height = frame["height"]!.GetValue<int>();
            if (x < 0 || y < 0 || x >= width || y >= height) throw new InvalidOperationException("POINT_OUTSIDE_SNAPSHOT");
            return (PointerGeometry.FromScreenshot(x, width, original.Item1, original.Item3),
                PointerGeometry.FromScreenshot(y, height, original.Item2, original.Item4));
        }
        // Cheap occlusion test for every point of a long gesture.
        void FastHit((int X, int Y) point)
        {
            if (GetAncestor(WindowFromPoint(new NativePoint { X = point.X, Y = point.Y }), 2) != hwnd)
                throw new InvalidOperationException("TARGET_OCCLUDED");
        }
        // Full accessibility hit test (password fields, foreign windows). Slower, so long gestures sample it.
        void HitTest((int X, int Y) point)
        {
            FastHit(point);
            var hit = AutomationElement.FromPoint(new Point(point.X, point.Y));
            for (var i = 0; hit is not null && i < 32; i++, hit = TreeWalker.RawViewWalker.GetParent(hit))
            {
                if (hit.Current.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
                if (hit.Current.NativeWindowHandle == hwnd.ToInt32()) break;
            }
        }
        var stepMs = 6;
        if (kind is "click" or "double_click" or "move" or "scroll") points.Add(anchor?.Point() ?? Map(action));
        else if (kind == "drag")
        {
            var path = action["path"]!.AsArray();
            if (path.Count is < 2 or > MaxStrokePoints) throw new InvalidOperationException("PATH_OUT_OF_RANGE");
            strokes.Add(path.Select(node => Map(node!)).ToList());
            stepMs = Math.Clamp(action["step_ms"]?.GetValue<int>() ?? 6, 2, 40);
        }
        else if (kind == "draw")
        {
            var drawn = action["strokes"]?.AsArray() ?? throw new InvalidOperationException("STROKES_REQUIRED");
            if (drawn.Count is < 1 or > MaxStrokes) throw new InvalidOperationException("STROKES_OUT_OF_RANGE");
            foreach (var stroke in drawn)
            {
                var path = stroke!["path"]!.AsArray();
                if (path.Count is < 2 or > MaxStrokePoints) throw new InvalidOperationException("PATH_OUT_OF_RANGE");
                strokes.Add(path.Select(node => Map(node!)).ToList());
            }
            if (strokes.Sum(s => s.Count) > MaxTotalPoints) throw new InvalidOperationException("PATH_TOO_LONG");
            stepMs = Math.Clamp(action["step_ms"]?.GetValue<int>() ?? 6, 2, 40);
        }
        foreach (var stroke in strokes) points.AddRange(stroke);
        if (strokes.Count > 0)
        {
            foreach (var point in points) FastHit(point);
            // Sample the accessibility tree at the start of each stroke; bounded so hit testing stays fast.
            foreach (var stroke in strokes.Take(24)) HitTest(stroke[0]);
        }
        else foreach (var point in points) HitTest(point);
        var scrollX = action["scroll_x"]?.GetValue<int>() ?? 0;
        var scrollY = action["scroll_y"]?.GetValue<int>() ?? 0;
        if (scrollX is < -2400 or > 2400 || scrollY is < -2400 or > 2400) throw new InvalidOperationException("SCROLL_OUT_OF_RANGE");
        if (kind is "keypress" or "type" or "type_text" && AutomationElement.FocusedElement?.Current.IsPassword == true)
            throw new InvalidOperationException("PASSWORD_TARGET");
        var before = state();
        Check();
        var currentCapture = JsonSerializer.SerializeToNode(before)!["capture"]!;
        if (!WindowCapture.Matches(frame, currentCapture, points)) throw new InvalidOperationException("SNAPSHOT_CHANGED");
        var button = action["button"]?.GetValue<string>() ?? "left";
        if (strokes.Count > 0)
        {
            // A light per-tick check: target identity and foreground, plus that the pointer is still over the window.
            void StrokeCheck() { Check(); FastHit(Cursor()); }
            (int X, int Y) Cursor() { GetCursorPos(out var p); return (p.X, p.Y); }
            GroundedInput.Draw(strokes, button, stepMs, StrokeCheck, (index, total) => Progress(kind == "draw" ? "Drawing" : "Dragging", index, total));
            Thread.Sleep(220); // Let the application finish painting the final stroke before observing.
        }
        else if (points.Count > 0)
        {
            void PointerCheck()
            {
                Check();
                foreach (var point in points) HitTest(point);
                if (anchor is not null) anchor.CheckHit(points[0].X, points[0].Y);
            }
            GroundedInput.Pointer(points, kind, button, scrollX, scrollY, PointerCheck);
        }
        else if (kind is "keypress" or "type" or "type_text")
        {
            var focused = AutomationElement.FocusedElement;
            if (focused is null || focused.Current.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
            var focusId = string.Join('.', focused.GetRuntimeId());
            if (focusId != frame["focused_element_id"]?.GetValue<string>()) throw new InvalidOperationException("FOCUS_CHANGED");
            void KeyboardCheck()
            {
                Check();
                var current = AutomationElement.FocusedElement;
                if (current is null || current.Current.IsPassword || string.Join('.', current.GetRuntimeId()) != focusId)
                    throw new InvalidOperationException("FOCUS_CHANGED");
            }
            if (kind == "keypress") GroundedInput.Chord(action["keys"]!.AsArray().Select(k => k!.GetValue<string>()).ToArray(), KeyboardCheck);
            else GroundedInput.Unicode(action["text"]!.GetValue<string>(), KeyboardCheck);
        }
        else throw new InvalidOperationException("UNSUPPORTED_ACTION");
        if (request["feedback"]?.GetValue<bool>() != false) ActionFeedback.Show(hwnd, kind, points.Count > 64 ? [points[0], points[^1]] : points);
        Thread.Sleep(100); // Capture after feedback closes and the target processes resulting hover events.
        object? after = null;
        string? observationError = null;
        try { after = state(); } catch (Exception ex) { observationError = ex is InvalidOperationException ? ex.Message : "OBSERVATION_FAILED"; }
        // Dispatch acknowledgement and pixel changes are not proof of task success.
        return new { dispatched = true, verified = false, before, after, observation_error = observationError, effect = "dispatched_unverified" };
    }

    [DllImport("user32.dll")] private static extern bool GetCursorPos(out NativePoint point);
}
