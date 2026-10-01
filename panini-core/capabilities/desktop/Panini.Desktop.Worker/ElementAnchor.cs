using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Automation;

// Only elements present in the broker's one-use receipt can anchor a graphical click.
internal sealed class ElementAnchor
{
    private readonly JsonNode observed;
    private readonly AutomationElement element;

    public ElementAnchor(string id, JsonNode frame, AutomationElement root)
    {
        var rows = frame["elements"]?.AsArray().Where(e => e?["element_id"]?.GetValue<string>() == id).ToArray();
        if (rows?.Length != 1) throw new InvalidOperationException("ELEMENT_NOT_OBSERVED");
        observed = rows[0]!;
        var matches = root.FindAll(TreeScope.Descendants, System.Windows.Automation.Condition.TrueCondition).Cast<AutomationElement>()
            .Where(e => string.Join('.', e.GetRuntimeId()) == id).Take(2).ToArray();
        if (matches.Length != 1) throw new InvalidOperationException("ELEMENT_CHANGED");
        element = matches[0];
        Validate();
    }

    public void Validate()
    {
        var c = element.Current;
        var b = c.BoundingRectangle;
        var expected = observed["bounds"]!;
        if (c.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
        if (!c.IsEnabled || c.IsOffscreen || b.IsEmpty || b.Width <= 0 || b.Height <= 0 ||
            c.Name[..Math.Min(c.Name.Length, 500)] != observed["name"]?.GetValue<string>() ||
            c.AutomationId != observed["automation_id"]?.GetValue<string>() ||
            c.ControlType.ProgrammaticName != observed["type"]?.GetValue<string>() ||
            b.X != expected["x"]!.GetValue<double>() || b.Y != expected["y"]!.GetValue<double>() ||
            b.Width != expected["width"]!.GetValue<double>() || b.Height != expected["height"]!.GetValue<double>())
            throw new InvalidOperationException("ELEMENT_CHANGED");
    }

    public (int X, int Y) Point()
    {
        var b = element.Current.BoundingRectangle;
        // Prefer the centre for small controls. The provider point is useful for clipped controls.
        var p = new Point(b.X + b.Width / 2, b.Y + b.Height / 2);
        if (!ContainsHit(p) && (!element.TryGetClickablePoint(out p) || !b.Contains(p) || !ContainsHit(p)))
            throw new InvalidOperationException("ELEMENT_OCCLUDED");
        return (checked((int)Math.Floor(p.X)), checked((int)Math.Floor(p.Y)));
    }

    public void CheckHit(int x, int y)
    {
        Validate();
        if (!ContainsHit(new Point(x, y))) throw new InvalidOperationException("ELEMENT_OCCLUDED");
    }

    // The element, everything above it up to the window, in the raw view.
    private List<AutomationElement>? containers;

    private List<AutomationElement> Containers()
    {
        if (containers is not null) return containers;
        containers = [];
        for (var parent = TreeWalker.RawViewWalker.GetParent(element); parent is not null && containers.Count < 64; parent = TreeWalker.RawViewWalker.GetParent(parent))
            containers.Add(parent);
        return containers;
    }

    /// <summary>
    /// True when the point lands on the element, inside it, or on a container that owns it.
    /// Modern XAML/WinUI applications (Paint, Settings) resolve hit tests only as far as their host
    /// pane, never to the individual button, so requiring the hit to be inside the element would
    /// refuse every click there. A hit on a different window, or on an element that does not own the
    /// target, is still refused, and the screenshot comparison separately catches new popups.
    /// </summary>
    private bool ContainsHit(Point point)
    {
        var hit = AutomationElement.FromPoint(point);
        var first = true;
        for (var i = 0; hit is not null && i < 64; i++, hit = TreeWalker.RawViewWalker.GetParent(hit))
        {
            if (hit.Current.IsPassword) throw new InvalidOperationException("PASSWORD_TARGET");
            if (Automation.Compare(hit, element)) return true;
            if (first && Containers().Any(container => Automation.Compare(container, hit))) return true;
            first = false;
        }
        return false;
    }
}
