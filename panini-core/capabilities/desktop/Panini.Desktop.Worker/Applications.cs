using System.Diagnostics;
using System.IO;
using System.Text.Json.Nodes;

// Fixed native application registrations. Requests carry IDs, never paths or shell commands.
internal static class Applications
{
    internal sealed record Registration(string Id, string Label, string Application, string Path, string Arguments);

    internal static List<Registration> Installed()
    {
        var registrations = new List<Registration>();
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var aliases = System.IO.Path.Combine(local, "Microsoft", "WindowsApps");
        Add("notepad", "Notepad", "notepad", "", System.IO.Path.Combine(system, "notepad.exe"), System.IO.Path.Combine(aliases, "notepad.exe"));
        Add("calculator", "Calculator", "calculatorapp", "", System.IO.Path.Combine(system, "calc.exe"));
        // Paint is a packaged app: its launcher is an App Execution Alias, and its window's process is mspaint.
        Add("paint", "Paint", "mspaint", "", System.IO.Path.Combine(aliases, "mspaint.exe"), System.IO.Path.Combine(system, "mspaint.exe"));
        Add("explorer", "File Explorer", "explorer", "", System.IO.Path.Combine(windows, "explorer.exe"));
        Add("vscode", "Visual Studio Code", "code", "",
            System.IO.Path.Combine(local, "Programs", "Microsoft VS Code", "Code.exe"),
            System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Microsoft VS Code", "Code.exe"));
        var terminal = System.IO.Path.Combine(aliases, "wt.exe");
        Add("terminal", "Windows Terminal", "windowsterminal", "", terminal);
        // PANINI-CLI runs in a Windows Terminal tab so it can be observed and driven like any terminal.
        var root = Environment.GetEnvironmentVariable("PANINI_RUNTIME_ROOT") ?? "";
        var cli = new[]
        {
            System.IO.Path.Combine(root, "cli", "PANINI-CLI.exe"),
            System.IO.Path.Combine(root, ".venv", "Scripts", "panini-cli.exe"),
        }.FirstOrDefault(File.Exists);
        if (cli is not null)
            Add("panini-cli", "PANINI-CLI", "windowsterminal", "new-tab --title PANINI-CLI \"" + cli + "\"", terminal);
        return registrations;

        void Add(string id, string label, string application, string arguments, params string[] paths)
        {
            var path = paths.FirstOrDefault(File.Exists);
            if (path is not null) registrations.Add(new(id, label, application, path, arguments));
        }
    }

    internal static object Catalog() => new { applications = Installed().Select(r => new { application_id = r.Id, label = r.Label, application = r.Application }) };

    internal static object Launch(JsonObject request)
    {
        var id = request["application_id"]?.GetValue<string>();
        var app = Installed().SingleOrDefault(r => r.Id == id) ?? throw new InvalidOperationException("APPLICATION_UNAVAILABLE");
        // ShellExecute launches this fixed GUI executable without inheriting the worker's
        // JSON stdio handles. Electron applications can otherwise corrupt the IPC response.
        var start = new ProcessStartInfo(app.Path) { UseShellExecute = true };
        if (app.Arguments.Length > 0) start.Arguments = app.Arguments;
        Environment.SetEnvironmentVariable("ELECTRON_RUN_AS_NODE", null);
        // Do not pass PANINI control credentials or provider keys into another application.
        foreach (var key in Environment.GetEnvironmentVariables().Keys.Cast<string>().Where(k => k.StartsWith("PANINI_") || k.StartsWith("JEV_REV_") || k.EndsWith("_KEY") || k.Contains("TOKEN") || k.Contains("SECRET")).ToArray()) Environment.SetEnvironmentVariable(key, null);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("APPLICATION_LAUNCH_FAILED");
        return new { application_id = app.Id, application = app.Application, launch_pid = process.Id };
    }
}
