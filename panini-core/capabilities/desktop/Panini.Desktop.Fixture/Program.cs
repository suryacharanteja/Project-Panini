using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Contains("--win32")) { Win32Fixture.Run(); return; }
        var app = new Application();
        if (args.Contains("--graphical")) { app.Run(new GraphicsFixture()); return; }
        var panel = new StackPanel { Margin = new Thickness(24) };
        var text = new TextBox { Text = "", Margin = new Thickness(0, 8, 0, 8) };
        AutomationProperties.SetAutomationId(text, "draft");
        AutomationProperties.SetName(text, "Draft text");
        var status = new TextBlock { Text = "No action performed" };
        AutomationProperties.SetAutomationId(status, "status");
        var button = new Button { Content = "Apply draft", Margin = new Thickness(0, 8, 0, 8) };
        AutomationProperties.SetAutomationId(button, "apply");
        button.Click += (_, _) => status.Text = "Applied: " + text.Text;
        panel.Children.Add(new TextBlock { Text = "PANINI Windows verification fixture", FontSize = 20 });
        panel.Children.Add(text); panel.Children.Add(button); panel.Children.Add(status);
        var password = new PasswordBox { Password = "fixture-secret", Margin = new Thickness(0, 8, 0, 8) };
        AutomationProperties.SetAutomationId(password, "password-fixture");
        AutomationProperties.SetName(password, "Private test field");
        panel.Children.Add(password);
        var toggle = new CheckBox { Content = "Enable fixture option", Margin = new Thickness(0, 8, 0, 8) };
        AutomationProperties.SetAutomationId(toggle, "option");
        panel.Children.Add(toggle);
        var list = new ListBox { Height = 75, Margin = new Thickness(0, 8, 0, 8) };
        foreach (var label in new[] { "Alpha", "Beta" })
        {
            var item = new ListBoxItem { Content = label };
            AutomationProperties.SetAutomationId(item, "choice-" + label.ToLowerInvariant());
            list.Items.Add(item);
        }
        panel.Children.Add(list);
        var rows = new StackPanel();
        for (var row = 1; row <= 30; row++) rows.Children.Add(new TextBlock { Text = $"Fixture row {row}", Height = 22 });
        var scroll = new ScrollViewer { Content = rows, Height = 100, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        AutomationProperties.SetAutomationId(scroll, "fixture-scroll");
        AutomationProperties.SetName(scroll, "Fixture rows");
        panel.Children.Add(scroll);
        app.Run(new Window { Title = "PANINI verification fixture", Width = 480, Height = 520, Content = panel });
    }
}
