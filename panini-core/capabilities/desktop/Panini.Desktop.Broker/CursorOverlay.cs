using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

/// <summary>
/// PANINI's visible hand on the desktop: a large cursor that glides where the real pointer goes,
/// ripples on every press, leaves a trail while it drags or draws, and says in words what it is doing.
///
/// It only observes the real pointer (position and button state), so it is always in step with the
/// input the worker actually sends. The window is click-through, never activated, has no automation
/// peer and no title, so it cannot intercept input, take focus or be targeted.
/// </summary>
internal sealed class CursorOverlay
{
    private enum Phase { Hidden, Observing, Thinking, Acting, Waiting, Done, Stopped }

    // Layout in device-independent pixels. The hotspot (the arrow's tip) is the pointer position.
    private const double WindowWidth = 720, WindowHeight = 300, HotX = 360, HotY = 90, ArrowScale = 1.9;
    private static readonly Color Copper = Color.FromRgb(0xB8, 0x73, 0x33);
    private static readonly Color Amber = Color.FromRgb(0xF2, 0xB2, 0x5C);
    private static readonly Color Gold = Color.FromRgb(0xD4, 0xAF, 0x37);
    private static readonly Color Obsidian = Color.FromRgb(0x14, 0x11, 0x0F);

    [StructLayout(LayoutKind.Sequential)] private struct NativePoint { public int X, Y; }
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out NativePoint point);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll")] private static extern int SetWindowLong(IntPtr hwnd, int index, int value);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);

    private sealed class OverlayWindow : Window
    {
        // No automation peer: accessibility tools and PANINI's own observers never see the overlay.
        protected override AutomationPeer? OnCreateAutomationPeer() => null;
    }

    private sealed class Ripple { public double Started, X, Y, Radius; public Ellipse Shape = new(); }

    private readonly Dispatcher dispatcher;
    private readonly ManualResetEventSlim ready = new();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private OverlayWindow window = null!;
    private IntPtr handle;
    private Canvas canvas = null!;
    private Polyline trailGlow = null!, trailCore = null!;
    private Ellipse waitRing = null!;
    private Canvas cursor = null!;
    private ScaleTransform cursorScale = null!;
    private TranslateTransform cursorMove = null!;
    private Border pill = null!;
    private TextBlock pillBrand = null!, pillText = null!;
    private readonly List<Ripple> ripples = [];
    private readonly Queue<(double T, double X, double Y)> trail = new();
    private Phase phase = Phase.Hidden;
    private string label = "";
    private double phaseStarted, lastFrame, alpha, smoothX, smoothY, press;
    private bool hasPosition, previousLeft, previousRight, reducedMotion;

    private CursorOverlay()
    {
        var thread = new Thread(Run) { IsBackground = true, Name = "PANINI cursor overlay" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!ready.Wait(TimeSpan.FromSeconds(8))) throw new InvalidOperationException("Overlay did not start");
        dispatcher = dispatcherHolder!;
    }

    private Dispatcher? dispatcherHolder;

    /// <summary>Starts the overlay, or returns null when it is disabled or cannot run (input still works).</summary>
    public static CursorOverlay? TryStart()
    {
        if (Environment.GetEnvironmentVariable("PANINI_DESKTOP_OVERLAY") == "0") return null;
        try { return new CursorOverlay(); }
        catch (Exception ex) { Console.Error.WriteLine("Cursor overlay unavailable: " + ex.Message); return null; }
    }

    private void Run()
    {
        try
        {
            dispatcherHolder = Dispatcher.CurrentDispatcher;
            reducedMotion = !SystemParameters.ClientAreaAnimation;
            Build();
            ready.Set();
            CompositionTarget.Rendering += (_, _) => Frame();
            Dispatcher.Run();
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("Cursor overlay stopped: " + ex.Message);
            ready.Set();
        }
    }

    private static Brush Solid(Color color, double opacity = 1)
    {
        var brush = new SolidColorBrush(color) { Opacity = opacity };
        brush.Freeze();
        return brush;
    }

    private void Build()
    {
        canvas = new Canvas { Width = WindowWidth, Height = WindowHeight, IsHitTestVisible = false };
        trailGlow = new Polyline { Stroke = Solid(Copper, 0.22), StrokeThickness = 16, StrokeLineJoin = PenLineJoin.Round, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round };
        trailCore = new Polyline { Stroke = Solid(Amber, 0.85), StrokeThickness = 5, StrokeLineJoin = PenLineJoin.Round, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round };
        waitRing = new Ellipse { Width = 60, Height = 60, Stroke = Solid(Amber, 0.9), StrokeThickness = 3, Visibility = Visibility.Collapsed };
        canvas.Children.Add(trailGlow);
        canvas.Children.Add(trailCore);
        for (var index = 0; index < 5; index++)
        {
            var ripple = new Ripple();
            ripple.Shape = new Ellipse { Stroke = Solid(Amber), StrokeThickness = 3, Visibility = Visibility.Collapsed };
            ripples.Add(ripple);
            canvas.Children.Add(ripple.Shape);
        }
        canvas.Children.Add(waitRing);

        // The arrow: tip at the origin, so the transform's translation is the pointer position.
        var arrow = Geometry.Parse("M0,0 L0,30 L7.5,23.5 L12.5,35 L17.5,32.8 L12.6,21.4 L22,21.4 Z");
        arrow.Freeze();
        cursorScale = new ScaleTransform(ArrowScale, ArrowScale);
        cursorMove = new TranslateTransform(HotX, HotY);
        cursor = new Canvas { RenderTransform = new TransformGroup { Children = { cursorScale, cursorMove } } };
        // Soft shadow, then a white keyline so the arrow reads on dark and light windows alike.
        cursor.Children.Add(new System.Windows.Shapes.Path { Data = arrow, Fill = Solid(Colors.Black, 0.28), RenderTransform = new TranslateTransform(2.2, 3.2) });
        cursor.Children.Add(new System.Windows.Shapes.Path { Data = arrow, Stroke = Solid(Colors.White), StrokeThickness = 4.2, StrokeLineJoin = PenLineJoin.Round });
        var gradient = new LinearGradientBrush(Amber, Copper, 65);
        gradient.Freeze();
        cursor.Children.Add(new System.Windows.Shapes.Path { Data = arrow, Fill = gradient, Stroke = Solid(Obsidian), StrokeThickness = 1.6, StrokeLineJoin = PenLineJoin.Round });
        canvas.Children.Add(cursor);

        var font = new FontFamily("Segoe UI Variable Text, Segoe UI");
        pillBrand = new TextBlock { FontFamily = font, Text = "PANINI", Foreground = Solid(Copper), FontWeight = FontWeights.Bold, FontSize = 11, Margin = new Thickness(0, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center };
        pillText = new TextBlock { FontFamily = font, Foreground = Solid(Color.FromRgb(0xF3, 0xED, 0xE7)), FontSize = 13, FontWeight = FontWeights.SemiBold, MaxWidth = 300, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(pillBrand);
        row.Children.Add(pillText);
        pill = new Border { Background = Solid(Obsidian, 0.94), BorderBrush = Solid(Copper), BorderThickness = new Thickness(1.2), CornerRadius = new CornerRadius(14), Padding = new Thickness(12, 6, 14, 6), Child = row };
        canvas.Children.Add(pill);

        window = new OverlayWindow
        {
            WindowStyle = WindowStyle.None, AllowsTransparency = true, Background = Brushes.Transparent, ShowActivated = false,
            ShowInTaskbar = false, Topmost = true, ResizeMode = ResizeMode.NoResize, Focusable = false, Title = "",
            Width = WindowWidth, Height = WindowHeight, Left = -32000, Top = -32000, Content = canvas, Opacity = 0,
        };
        handle = new WindowInteropHelper(window).EnsureHandle();
        // TRANSPARENT (click-through), TOOLWINDOW (no Alt+Tab entry), NOACTIVATE (never takes focus).
        SetWindowLong(handle, -20, GetWindowLong(handle, -20) | 0x20 | 0x80 | 0x08000000);
    }

    private static string DefaultLabel(Phase value) => value switch
    {
        Phase.Observing => "Looking at the screen",
        Phase.Thinking => "Thinking…",
        Phase.Waiting => "Waiting for your approval",
        Phase.Done => "Done",
        Phase.Stopped => "Stopped",
        _ => "Working",
    };

    /// <summary>Shows or updates the overlay. Phases: observing, thinking, acting, waiting, done, stopped, hidden.</summary>
    public void Set(string phaseName, string message)
    {
        var next = phaseName.ToLowerInvariant() switch
        {
            "observing" => Phase.Observing, "thinking" => Phase.Thinking, "acting" => Phase.Acting, "waiting" => Phase.Waiting,
            "done" => Phase.Done, "stopped" or "error" => Phase.Stopped, _ => Phase.Hidden,
        };
        dispatcher.BeginInvoke(() =>
        {
            phase = next;
            label = message.Length > 0 ? message : DefaultLabel(next);
            phaseStarted = clock.Elapsed.TotalSeconds;
            pillText.Text = label;
            pillBrand.Foreground = Solid(next == Phase.Done ? Gold : Copper);
            if (next != Phase.Hidden && !window.IsVisible)
            {
                hasPosition = false;
                alpha = 0;
                window.Show();
            }
        });
    }

    /// <summary>
    /// A stop request ends the overlay's story as "Stopped", but only while work is actually in progress:
    /// the cleanup stop sent after every finished task must not turn "Done" into "Stopped".
    /// </summary>
    public void Stop()
    {
        dispatcher.BeginInvoke(() =>
        {
            if (phase is Phase.Hidden or Phase.Done or Phase.Stopped) return;
            phase = Phase.Stopped;
            label = DefaultLabel(Phase.Stopped);
            phaseStarted = clock.Elapsed.TotalSeconds;
            pillText.Text = label;
        });
    }

    /// <summary>Updates only the words, for example "Drawing 3/9", keeping the current phase.</summary>
    public void Progress(string message)
    {
        dispatcher.BeginInvoke(() => { label = message; pillText.Text = message; phaseStarted = clock.Elapsed.TotalSeconds; });
    }

    private void AddRipple(double now, double x, double y, double radius)
    {
        var ripple = ripples.OrderBy(r => r.Started).First();
        ripple.Started = now; ripple.X = x; ripple.Y = y; ripple.Radius = radius;
        ripple.Shape.Visibility = Visibility.Visible;
    }

    private void Frame()
    {
        var now = clock.Elapsed.TotalSeconds;
        var dt = Math.Clamp(now - lastFrame, 0.001, 0.05);
        lastFrame = now;
        if (phase == Phase.Done && now - phaseStarted > 1.15) phase = Phase.Hidden;
        if (phase == Phase.Stopped && now - phaseStarted > 1.6) phase = Phase.Hidden;
        // If PANINI goes silent, never leave a cursor on someone's screen indefinitely.
        var patience = phase == Phase.Waiting ? 900 : 90;
        if (phase is not (Phase.Hidden or Phase.Done or Phase.Stopped) && now - phaseStarted > patience) phase = Phase.Hidden;
        var visible = phase != Phase.Hidden;
        alpha += ((visible ? 1 : 0) - alpha) * (1 - Math.Exp(-dt * 11));
        if (!visible && alpha < 0.02)
        {
            alpha = 0;
            if (window.IsVisible) window.Hide();
            return;
        }
        GetCursorPos(out var real);
        if (!hasPosition) { smoothX = real.X; smoothY = real.Y; hasPosition = true; trail.Clear(); }
        var follow = reducedMotion ? 1 : 1 - Math.Exp(-dt * 26);
        smoothX += (real.X - smoothX) * follow;
        smoothY += (real.Y - smoothY) * follow;

        var scale = VisualTreeHelper.GetDpi(window).DpiScaleX;
        var left = (GetAsyncKeyState(0x01) & 0x8000) != 0;
        var right = (GetAsyncKeyState(0x02) & 0x8000) != 0;
        if ((left && !previousLeft) || (right && !previousRight)) AddRipple(now, real.X, real.Y, 62);
        else if ((!left && previousLeft) || (!right && previousRight)) AddRipple(now, real.X, real.Y, 34);
        previousLeft = left; previousRight = right;

        // Trail: a long amber comet while the button is down (drawing/dragging), a brief glint otherwise.
        var moved = trail.Count == 0 || Math.Abs(trail.Last().X - smoothX) + Math.Abs(trail.Last().Y - smoothY) > 1.5;
        if (moved) trail.Enqueue((now, smoothX, smoothY));
        var life = left || right ? 1.1 : 0.22;
        while (trail.Count > 0 && now - trail.Peek().T > life) trail.Dequeue();
        var points = new PointCollection();
        foreach (var (_, x, y) in trail) points.Add(new System.Windows.Point(HotX + (x - smoothX) / scale, HotY + (y - smoothY) / scale));
        if (reducedMotion || points.Count < 2) points = new PointCollection();
        trailGlow.Points = points;
        trailCore.Points = points;

        foreach (var ripple in ripples)
        {
            var progress = (now - ripple.Started) / 0.6;
            if (progress is < 0 or >= 1 || ripple.Shape.Visibility != Visibility.Visible) { ripple.Shape.Visibility = Visibility.Collapsed; continue; }
            var eased = 1 - Math.Pow(1 - progress, 3);
            var radius = 10 + ripple.Radius * eased;
            ripple.Shape.Width = ripple.Shape.Height = radius * 2;
            ripple.Shape.Opacity = 0.85 * (1 - progress);
            Canvas.SetLeft(ripple.Shape, HotX + (ripple.X - smoothX) / scale - radius);
            Canvas.SetTop(ripple.Shape, HotY + (ripple.Y - smoothY) / scale - radius);
        }

        // Press squeezes the arrow; idle and thinking breathe gently; waiting pulses an amber ring.
        press += ((left || right ? 1 : 0) - press) * (1 - Math.Exp(-dt * 30));
        var breathe = reducedMotion || phase is Phase.Acting ? 0 : Math.Sin(now * Math.PI * 2 / 1.7) * 0.035;
        var size = ArrowScale * (1 - 0.14 * press + breathe);
        cursorScale.ScaleX = cursorScale.ScaleY = size;
        waitRing.Visibility = phase == Phase.Waiting ? Visibility.Visible : Visibility.Collapsed;
        if (phase == Phase.Waiting)
        {
            var pulse = 30 + (reducedMotion ? 0 : 7 * Math.Sin(now * Math.PI * 2 / 1.3));
            waitRing.Width = waitRing.Height = pulse * 2;
            Canvas.SetLeft(waitRing, HotX - pulse);
            Canvas.SetTop(waitRing, HotY - pulse);
        }

        // Keep the words on screen: flip the pill left or above near the display edges.
        pill.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        var pillWidth = pill.DesiredSize.Width;
        var screenRight = GetSystemMetrics(76) + GetSystemMetrics(78);
        var screenBottom = GetSystemMetrics(77) + GetSystemMetrics(79);
        var flipLeft = smoothX + (46 + pillWidth + 24) * scale > screenRight;
        var flipUp = smoothY + 110 * scale > screenBottom;
        Canvas.SetLeft(pill, flipLeft ? HotX - 14 - pillWidth : HotX + 40);
        Canvas.SetTop(pill, flipUp ? HotY - 58 : HotY + 60);

        window.Opacity = alpha;
        SetWindowPos(handle, new IntPtr(-1), (int)Math.Round(smoothX - HotX * scale), (int)Math.Round(smoothY - HotY * scale), 0, 0,
            0x0001 | 0x0010 | 0x0200 | 0x0040); // NOSIZE | NOACTIVATE | NOOWNERZORDER | SHOWWINDOW
    }
}
