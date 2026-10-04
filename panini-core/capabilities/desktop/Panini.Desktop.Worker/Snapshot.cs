using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Windows.Automation;

internal static partial class Program
{
    private static readonly Regex CredentialLabel = new(@"password|passcode|one.time|\bpin\b|\bcvv\b|\bcvc\b|\botp\b|card.number|security.code|verification.code", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static bool Credential(string name, string automationId) => CredentialLabel.IsMatch(name + " " + automationId);
    private static AutomationElement[] FineTargets(AutomationElement root, string identifier)
    {
        var found = new List<AutomationElement>();
        var walker = TreeWalker.ControlViewWalker;
        var stack = new Stack<(AutomationElement Element, int Depth)>();
        stack.Push((root, 0));
        var visited = 0;
        while (stack.Count > 0)
        {
            var (element, depth) = stack.Pop();
            if (++visited > 2400 || depth > 30) throw new InvalidOperationException("TARGET_TREE_LIMIT");
            var current = element.Current;
            if (current.IsOffscreen || !Automation.Compare(root, element) && !VisibleWithinAncestors(element, root)) continue;
            if (string.Join('.', element.GetRuntimeId()) == identifier) { found.Add(element); if (found.Count == 2) break; }
            if (current.IsPassword || Credential(current.Name, current.AutomationId)) continue;
            var child = walker.GetFirstChild(element);
            while (child is not null)
            {
                stack.Push((child, depth + 1));
                if (stack.Count > 2400) throw new InvalidOperationException("TARGET_TREE_LIMIT");
                child = walker.GetNextSibling(child);
            }
        }
        return found.ToArray();
    }
    private static void ValidateFineState(AutomationElement element, JsonNode action)
    {
        var states = new List<string>();
        string? value = null;
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var p)) value = ((ValuePattern)p).Current.Value;
        else if (element.TryGetCurrentPattern(TextPattern.Pattern, out p)) value = ((TextPattern)p).DocumentRange.GetText(2000);
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out p) && ((TogglePattern)p).Current.ToggleState == ToggleState.On) states.Add("checked");
        if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p) && ((SelectionItemPattern)p).Current.IsSelected) states.Add("selected");
        if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out p) && ((ExpandCollapsePattern)p).Current.ExpandCollapseState == ExpandCollapseState.Expanded) states.Add("expanded");
        if (!states.SequenceEqual(action["expected_states"]!.AsArray().Select(n => n!.GetValue<string>()))
            || value?[..Math.Min(value.Length, 2000)] != action["expected_value"]?.GetValue<string>())
            throw new InvalidOperationException("ELEMENT_CHANGED");
    }

    private static void ValidateBoundsHit(AutomationElement root, AutomationElement target, int x, int y)
    {
        var walker = TreeWalker.ControlViewWalker;
        var stack = new Stack<(AutomationElement Element, int Depth, bool Inside)>();
        stack.Push((root, 0, false));
        var visited = 0;
        var deepest = -1;
        var insideTarget = false;
        while (stack.Count > 0)
        {
            var (element, depth, inside) = stack.Pop();
            if (++visited > 2400 || depth > 30) throw new InvalidOperationException("HIT_TEST_LIMIT");
            var current = element.Current;
            if (current.IsOffscreen || !current.BoundingRectangle.Contains(x, y)) continue;
            if (current.IsPassword || Credential(current.Name, current.AutomationId)) throw new InvalidOperationException("PASSWORD_TARGET");
            inside |= Automation.Compare(element, target);
            if (depth >= deepest) { deepest = depth; insideTarget = inside; }
            var child = walker.GetFirstChild(element);
            while (child is not null)
            {
                stack.Push((child, depth + 1, inside));
                if (stack.Count > 2400) throw new InvalidOperationException("HIT_TEST_LIMIT");
                child = walker.GetNextSibling(child);
            }
        }
        if (!insideTarget) throw new InvalidOperationException("ELEMENT_OCCLUDED");
    }

    private static object Snapshot(JsonObject request)
    {
        var (hwnd, root) = Target(request);
        var maxDepth = request["depth"]?.GetValue<int>() ?? 20;
        var maxNodes = request["max_nodes"]?.GetValue<int>() ?? 600;
        if (maxDepth is < 1 or > 30 || maxNodes is < 1 or > 600) throw new InvalidOperationException("INVALID_SNAPSHOT_LIMIT");
        var rows = new List<object>();
        var walker = TreeWalker.ControlViewWalker;
        var visited = 0;
        var truncated = false;
        var focused = AutomationElement.FocusedElement;
        string? focusedId = null;
        void Walk(AutomationElement element, int sourceDepth, int outputDepth, bool isRoot = false)
        {
            if (++visited > 2400 || rows.Count >= maxNodes) { truncated = true; return; }
            var c = element.Current;
            if (!isRoot && (c.IsOffscreen || !VisibleWithinAncestors(element, root))) return;
            var password = c.IsPassword || Credential(c.Name, c.AutomationId);
            var type = c.ControlType.ProgrammaticName;
            var patterns = element.GetSupportedPatterns().Select(p => p.ProgrammaticName).ToArray();
            var keep = isRoot || password || c.Name.Length > 0 || patterns.Length > 0
                || c.ControlType != ControlType.Custom && c.ControlType != ControlType.Pane && c.ControlType != ControlType.Group;
            if (keep)
            {
                string? value = null;
                var states = new List<string>();
                if (password) states.Add("password");
                if (!c.IsEnabled) states.Add("disabled");
                if (!password)
                {
                    if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var p)) value = ((ValuePattern)p).Current.Value;
                    else if (element.TryGetCurrentPattern(TextPattern.Pattern, out p)) value = ((TextPattern)p).DocumentRange.GetText(2000);
                    if (element.TryGetCurrentPattern(TogglePattern.Pattern, out p) && ((TogglePattern)p).Current.ToggleState == ToggleState.On) states.Add("checked");
                    if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p) && ((SelectionItemPattern)p).Current.IsSelected) states.Add("selected");
                    if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out p) && ((ExpandCollapsePattern)p).Current.ExpandCollapseState == ExpandCollapseState.Expanded) states.Add("expanded");
                }
                var id = string.Join('.', element.GetRuntimeId());
                var b = c.BoundingRectangle;
                var isFocused = focused is not null && Automation.Compare(focused, element);
                if (isFocused && !password) focusedId = id;
                rows.Add(new { element_id = id, name = password ? "Password field" : c.Name[..Math.Min(c.Name.Length, 500)],
                    automation_id = c.AutomationId, type, framework = c.FrameworkId, depth = outputDepth, value = value?[..Math.Min(value.Length, 2000)],
                    states, enabled = c.IsEnabled, focused = isFocused, patterns,
                    bounds = new { x = b.X, y = b.Y, width = b.Width, height = b.Height } });
            }
            // A password provider may expose its content through descendants. Do not traverse it.
            if (password) return;
            var child = walker.GetFirstChild(element);
            if (sourceDepth >= maxDepth) { if (child is not null) truncated = true; return; }
            while (child is not null)
            {
                Walk(child, sourceDepth + 1, outputDepth + (keep ? 1 : 0));
                if (visited >= 2400 || rows.Count >= maxNodes) { truncated = true; break; }
                child = walker.GetNextSibling(child);
            }
        }
        Walk(root, 0, 0, true);
        Target(request);
        return new { observation_id = Guid.NewGuid().ToString(), observed_at = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            foreground = TargetForeground(hwnd), title = root.Current.Name, nodes = rows, truncated, focused_element_id = focusedId };
    }
}
