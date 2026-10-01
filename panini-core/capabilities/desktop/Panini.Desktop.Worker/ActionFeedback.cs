using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

// Short, static feedback after dispatch: never takes focus or intercepts input.
// Static rendering also respects reduced-motion users without requiring animation.
internal static class ActionFeedback
{
    [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll")] private static extern int SetWindowLong(IntPtr hwnd, int index, int value);

    public static void Show(IntPtr target, string kind, IReadOnlyList<(int X, int Y)> points)
    {
        if (Environment.GetEnvironmentVariable("PANINI_DESKTOP_FEEDBACK") == "0") return;
        var thread = new Thread(() =>
        {
            try
            {
                var bounds = WindowCapture.Bounds(target);
                var canvas = new Canvas { IsHitTestVisible = false };
                var window = new Window { WindowStyle = WindowStyle.None, AllowsTransparency = true, Background = Brushes.Transparent,
                    ShowActivated = false, ShowInTaskbar = false, Topmost = true, ResizeMode = ResizeMode.NoResize,
                    Left = bounds.X, Top = bounds.Y, Width = bounds.Width, Height = bounds.Height, Content = canvas };
                window.SourceInitialized += (_, _) =>
                {
                    var handle = new WindowInteropHelper(window).Handle;
                    SetWindowLong(handle, -20, GetWindowLong(handle, -20) | 0x20 | 0x80 | 0x08000000);
                    var source = HwndSource.FromHwnd(handle);
                    var matrix = source.CompositionTarget.TransformFromDevice;
                    var origin = matrix.Transform(new Point(bounds.X, bounds.Y));
                    var size = matrix.Transform(new Point(bounds.Width, bounds.Height));
                    window.Left = origin.X; window.Top = origin.Y; window.Width = size.X; window.Height = size.Y;
                    var accent = new SolidColorBrush(Color.FromRgb(70, 225, 196));
                    var path = new Polyline { Stroke = accent, StrokeThickness = 3, StrokeLineJoin = PenLineJoin.Round, Opacity = .85 };
                    foreach (var p in points) path.Points.Add(matrix.Transform(new Point(p.X - bounds.X, p.Y - bounds.Y)));
                    if (path.Points.Count > 1) canvas.Children.Add(path);
                    foreach (var p in path.Points.Take(1).Concat(path.Points.Count > 1 ? path.Points.TakeLast(1) : []))
                    {
                        var ring = new Ellipse { Width = 24, Height = 24, Stroke = accent, StrokeThickness = 3 };
                        Canvas.SetLeft(ring, p.X - 12); Canvas.SetTop(ring, p.Y - 12); canvas.Children.Add(ring);
                    }
                    var badge = new Border { CornerRadius = new CornerRadius(12), Background = new SolidColorBrush(Color.FromRgb(18, 28, 43)),
                        Padding = new Thickness(14, 8, 14, 8), Child = new TextBlock { Text = "PANINI  ·  " + kind.Replace('_', ' ') + "  ·  sent", Foreground = Brushes.White, FontSize = 13 } };
                    Canvas.SetLeft(badge, 16); Canvas.SetTop(badge, 40); canvas.Children.Add(badge);
                };
                var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(280) };
                timer.Tick += (_, _) => { timer.Stop(); window.Close(); Dispatcher.CurrentDispatcher.BeginInvokeShutdown(DispatcherPriority.Background); };
                window.Show(); timer.Start(); Dispatcher.Run();
            }
            catch (Exception) { /* Feedback cannot change action success or trigger a retry. */ }
        }) { IsBackground = true };
        thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join(750);
    }
}
