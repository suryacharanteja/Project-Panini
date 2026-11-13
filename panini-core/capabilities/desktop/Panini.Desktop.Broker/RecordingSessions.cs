using System.Diagnostics;
using System.Text.Json.Nodes;

// One explicit owner recording; disposable action workers remain short-lived.
internal sealed class RecordingSessions(string worker)
{
    private readonly SemaphoreSlim gate = new(1);
    private Process? process;
    private string? task;
    private string? token;
    private Task<string?>? final;
    private long expires;
    private int recording;
    private CancellationTokenSource? lifetime;
    public bool Active => Volatile.Read(ref recording) != 0;

    public async Task<JsonNode> Start(JsonObject request)
    {
        await gate.WaitAsync();
        try
        {
            if (Active) return new JsonObject { ["ok"] = false, ["error"] = "RECORDING_BUSY" };
            Cleanup();
            Volatile.Write(ref recording, 1);
            task = request["task_id"]!.GetValue<string>();
            token = Guid.NewGuid().ToString("N");
            expires = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 900000;
            request["op"] = "record";
            request["owner_pid"] = Environment.ProcessId;
            using var owner = Process.GetCurrentProcess();
            request["owner_created"] = owner.StartTime.ToUniversalTime().Ticks.ToString();
            process = new Process { StartInfo = new ProcessStartInfo(worker) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true } };
            foreach (var key in process.StartInfo.Environment.Keys.Where(k => k.EndsWith("_KEY") || k.Contains("TOKEN") || k.Contains("SECRET")).ToArray()) process.StartInfo.Environment.Remove(key);
            process.Start();
            process.ErrorDataReceived += (_, _) => { }; process.BeginErrorReadLine();
            await process.StandardInput.WriteLineAsync(request.ToJsonString());
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            var ready = await process.StandardOutput.ReadLineAsync(deadline.Token);
            if (ready is null || ready.Length > 1000000) throw new InvalidOperationException("RECORDING_WORKER_FAILED");
            var result = JsonNode.Parse(ready)!;
            if (result["ok"]?.GetValue<bool>() != true) throw new InvalidOperationException(result["error"]?.GetValue<string>() ?? "RECORDING_WORKER_FAILED");
            final = process.StandardOutput.ReadLineAsync();
            lifetime = new CancellationTokenSource();
            _ = Expire(token, lifetime.Token);
            result["result"]!["recording_id"] = token;
            result["result"]!["expires_in"] = 900;
            return result;
        }
        catch (Exception ex) { Cleanup(); return new JsonObject { ["ok"] = false, ["error"] = ex is InvalidOperationException ? ex.Message : "RECORDING_WORKER_FAILED" }; }
        finally { gate.Release(); }
    }

    public async Task<JsonNode> Stop(string ownerTask, string? id, bool discard = false)
    {
        await gate.WaitAsync();
        try
        {
            if (process is null) return new JsonObject { ["ok"] = true, ["result"] = new JsonObject { ["state"] = "idle" } };
            if (ownerTask != task || !discard && id != token) return new JsonObject { ["ok"] = false, ["error"] = "RECORDING_CHANGED" };
            try
            {
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
                if (!process.HasExited) { await process.StandardInput.WriteLineAsync("stop"); process.StandardInput.Close(); }
                var text = await final!.WaitAsync(deadline.Token);
                await process.WaitForExitAsync(deadline.Token);
                if (text is null || text.Length > 1000000) throw new InvalidOperationException("RECORDING_WORKER_FAILED");
                var result = JsonNode.Parse(text)!;
                return discard ? new JsonObject { ["ok"] = true, ["result"] = new JsonObject { ["state"] = "discarded" } } : result;
            }
            catch (Exception) { return new JsonObject { ["ok"] = false, ["error"] = "RECORDING_INTERRUPTED" }; }
            finally { Cleanup(); }
        }
        finally { gate.Release(); }
    }

    private void Cleanup()
    {
        lifetime?.Cancel(); lifetime?.Dispose(); lifetime = null;
        try { if (process is not null && !process.HasExited) process.Kill(true); }
        catch (InvalidOperationException) { }
        process?.Dispose(); process = null; task = token = null; final = null;
        Volatile.Write(ref recording, 0);
    }

    private async Task Expire(string id, CancellationToken cancelled)
    {
        try
        {
            await Task.Delay(TimeSpan.FromMilliseconds(Math.Max(1, expires - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())), cancelled);
            await gate.WaitAsync(cancelled);
            try { if (token == id) Cleanup(); } finally { gate.Release(); }
        }
        catch (OperationCanceledException) { }
    }
}
