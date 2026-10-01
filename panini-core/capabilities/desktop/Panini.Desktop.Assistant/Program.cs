using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

internal sealed class ReadinessSnapshot
{
    public bool Interactive { get; init; }
    public bool DefaultDesktop { get; init; }
    public bool UiAutomation { get; init; }
    public bool Capture { get; init; }
    public string Recovery { get; init; } = "";
}

internal static class Program
{
    [DllImport("user32.dll")] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool GetUserObjectInformation(IntPtr handle, int kind, StringBuilder value, uint bytes, out uint needed);

    [STAThread]
    private static void Main()
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
        var title = new TextBlock { Text = "PANINI Windows readiness", FontSize = 22, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 16) };
        var subtitle = new TextBlock { Text = "Computer use needs an unlocked interactive desktop.", Foreground = Brushes.DimGray, Margin = new Thickness(0, 0, 0, 18) };
        var checks = new StackPanel();
        var recovery = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 16, 0, 0) };
        var refresh = new Button { Content = "Refresh readiness", Padding = new Thickness(14, 8, 14, 8), HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 18, 0, 0) };
        void Render()
        {
            var state = Check();
            checks.Children.Clear();
            Add("Interactive user session", state.Interactive);
            Add("Default input desktop", state.DefaultDesktop);
            Add("UI Automation worker", state.UiAutomation);
            Add("Window capture", state.Capture);
            recovery.Text = state.Recovery;
            void Add(string label, bool ok) => checks.Children.Add(new TextBlock { Text = (ok ? "●  " : "○  ") + label, Foreground = ok ? Brushes.SeaGreen : Brushes.DarkOrange, FontSize = 15, Margin = new Thickness(0, 4, 0, 4) });
        }
        refresh.Click += (_, _) => Render();
        var content = new StackPanel { Margin = new Thickness(28) };
        content.Children.Add(title); content.Children.Add(subtitle); content.Children.Add(checks); content.Children.Add(recovery); content.Children.Add(refresh);
        var window = new Window { Title = "PANINI Desktop Readiness", Width = 430, Height = 390, ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterScreen, Content = content };
        Render(); app.Run(window);
    }

    private static ReadinessSnapshot Check()
    {
        var interactive = Environment.UserInteractive;
        var defaultDesktop = false;
        var desktop = OpenInputDesktop(0, false, 1);
        if (desktop != IntPtr.Zero)
        {
            try { var name = new StringBuilder(256); defaultDesktop = GetUserObjectInformation(desktop, 2, name, 1024, out _) && name.ToString() == "Default"; }
            finally { CloseDesktop(desktop); }
        }
        var recovery = interactive && defaultDesktop
            ? "PANINI can interact with the selected standard-user window. Keep the target unlocked and visible before sending input."
            : "Unlock Windows and return to the normal user desktop, then press Refresh readiness. PANINI will not change Windows security or privacy settings.";
        return new ReadinessSnapshot { Interactive = interactive, DefaultDesktop = defaultDesktop, UiAutomation = interactive && defaultDesktop, Capture = interactive && defaultDesktop, Recovery = recovery };
    }
}
