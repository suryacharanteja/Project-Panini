using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;

internal sealed class GraphicsFixture : Window
{
    public GraphicsFixture()
    {
        Title = "PANINI graphical verification"; Width = 940; Height = 710;
        Background = Brush("#111827"); Foreground = Brushes.White; Left = 80; Top = 50;
        var root = new StackPanel { Margin = new Thickness(28) }; Content = root;
        root.Children.Add(new TextBlock { Text = "PANINI  /  Interaction studio", FontSize = 25, FontWeight = FontWeights.SemiBold });
        root.Children.Add(new TextBlock { Text = "Windows input · Screenshot coordinates · Observable results", Foreground = Brush("#A6B4C8"), Margin = new Thickness(0, 8, 0, 20) });
        var precisionRow = new StackPanel { Orientation = Orientation.Horizontal };
        var precisionStatus = new TextBlock { Text = "Target -1; Hits 0", Margin = new Thickness(12, 0, 0, 0) };
        AutomationProperties.SetAutomationId(precisionStatus, "precision-status");
        var precisionHits = 0;
        for (var index = 0; index < 8; index++)
        {
            var targetIndex = index;
            var button = new Button { Content = index.ToString(), Width = 14, Height = 18, Padding = new Thickness(0), FontSize = 8 };
            AutomationProperties.SetAutomationId(button, "precision-" + index);
            button.Click += (_, _) => precisionStatus.Text = $"Target {targetIndex}; Hits {++precisionHits}";
            precisionRow.Children.Add(button);
        }
        precisionRow.Children.Add(precisionStatus);
        root.Children.Add(precisionRow);
        var status = new TextBlock { Text = "Clicks 0 · Double 0 · Right 0 · Moves 0 · Drags 0 · Wheel 0,0", FontSize = 14, Foreground = Brush("#46E1C4"), Margin = new Thickness(0, 8, 0, 10) };
        AutomationProperties.SetAutomationId(status, "graphics-status");
        var canvas = new GestureCanvas { Background = Brush("#1D293C"), Height = 250, ClipToBounds = true, Focusable = true };
        AutomationProperties.SetAutomationId(canvas, "graphics-board"); AutomationProperties.SetName(canvas, "Gesture board");
        for (var x = 0; x < 900; x += 30) canvas.Children.Add(new Line { X1 = x, X2 = x, Y1 = 0, Y2 = 250, Stroke = Brush("#27354A"), StrokeThickness = 1 });
        for (var y = 0; y < 250; y += 30) canvas.Children.Add(new Line { X1 = 0, X2 = 900, Y1 = y, Y2 = y, Stroke = Brush("#27354A"), StrokeThickness = 1 });
        Polyline? stroke = null;
        Point? lastPosition = null;
        var moves = 0; var drags = 0; var clicks = 0; var doubles = 0; var right = 0; var wheelX = 0; var wheelY = 0;
        void Update() => status.Text = $"Clicks {clicks} · Double {doubles} · Right {right} · Moves {moves} · Drags {drags} · Wheel {wheelX},{wheelY}";
        canvas.MouseDown += (_, e) =>
        {
            canvas.Focus(); clicks++; if (e.ClickCount == 2) doubles++; if (e.ChangedButton == MouseButton.Right) right++;
            if (e.ChangedButton == MouseButton.Left)
            {
                stroke = new Polyline { Stroke = Brush("#46E1C4"), StrokeThickness = 4, StrokeLineJoin = PenLineJoin.Round };
                stroke.Points.Add(e.GetPosition(canvas)); canvas.Children.Add(stroke); canvas.CaptureMouse();
            }
            Update(); e.Handled = true;
        };
        canvas.MouseMove += (_, e) =>
        {
            var position = e.GetPosition(canvas);
            // WPF can raise MouseMove after layout/capture without physical movement.
            if (position == lastPosition) return;
            lastPosition = position; moves++;
            if (e.LeftButton == MouseButtonState.Pressed) stroke?.Points.Add(position);
            Update();
        };
        canvas.MouseUp += (_, e) => { if (stroke?.Points.Count > 2) drags++; stroke = null; canvas.ReleaseMouseCapture(); Update(); e.Handled = true; };
        canvas.MouseWheel += (_, e) => { wheelY += e.Delta; Update(); e.Handled = true; };
        root.Children.Add(canvas); root.Children.Add(status);
        var edit = new TextBox { Text = "Select and replace this draft", Height = 90, AcceptsReturn = true, AcceptsTab = true, Background = Brush("#1D293C"), Foreground = Brushes.White, Padding = new Thickness(10), BorderBrush = Brush("#3A4D67"), CaretBrush = Brushes.White };
        AutomationProperties.SetAutomationId(edit, "graphics-draft"); AutomationProperties.SetName(edit, "Graphical draft");
        root.Children.Add(edit);
        var keyStatus = new TextBlock { Text = "Keys: none", Foreground = Brush("#A6B4C8"), Margin = new Thickness(0, 10, 0, 8) };
        AutomationProperties.SetAutomationId(keyStatus, "graphics-keys");
        PreviewKeyDown += (_, e) => keyStatus.Text = "Keys: " + Keyboard.Modifiers + "+" + (e.Key == Key.System ? e.SystemKey : e.Key);
        root.Children.Add(keyStatus);
        var password = new PasswordBox { Width = 220, HorizontalAlignment = HorizontalAlignment.Left, Password = "fixture-only", Height = 25 };
        AutomationProperties.SetAutomationId(password, "graphics-password");
        root.Children.Add(password);
        SourceInitialized += (_, _) => HwndSource.FromHwnd(new WindowInteropHelper(this).Handle).AddHook((IntPtr hwnd, int message, IntPtr wparam, IntPtr lparam, ref bool handled) =>
        {
            if (message == 0x20E) { wheelX += (short)((wparam.ToInt64() >> 16) & 0xffff); Update(); handled = true; }
            return IntPtr.Zero;
        });
    }
    private static SolidColorBrush Brush(string value) => new((Color)ColorConverter.ConvertFromString(value));
    private sealed class GestureCanvas : Canvas
    {
        protected override AutomationPeer OnCreateAutomationPeer() => new FrameworkElementAutomationPeer(this);
    }
}
