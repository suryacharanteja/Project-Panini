using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;

var name = Environment.GetEnvironmentVariable("PANINI_DESKTOP_PIPE") ?? throw new InvalidOperationException("Pipe is required");
var secret = Environment.GetEnvironmentVariable("PANINI_DESKTOP_SECRET") ?? throw new InvalidOperationException("Launch credential is required");
if (secret.Length < 32 || !name.StartsWith("panini-")) throw new InvalidOperationException("Invalid runtime identity");
var worker = Environment.GetEnvironmentVariable("PANINI_DESKTOP_WORKER") ?? System.IO.Path.Combine(AppContext.BaseDirectory, "worker", "Panini.Desktop.Worker.exe");
Native.PerMonitorDpi();
// The visible cursor is cosmetic: if it cannot start, input, observation and verification are unaffected.
var overlay = CursorOverlay.TryStart();
var active = new ConcurrentDictionary<string, CancellationTokenSource>();
var completed = new ConcurrentDictionary<string, TaskCompletionSource>();
var stopped = new ConcurrentDictionary<string, long>();
var seen = new ConcurrentDictionary<string, long>();
var permits = new SemaphoreSlim(4);
var inputPermit = new SemaphoreSlim(1);
var snapshots = new ConcurrentDictionary<string, (string Target, string Task, long Expires, JsonNode Frame)>();
var fineSnapshots = new FineSnapshots();
// An ordinary request may not outlive 12 seconds. Only a drawing or dragging gesture, which is deliberately
// paced so applications register a continuous line, may run for up to a minute.
const long ShortWindowMs = 12000, GestureWindowMs = 60000;
while (true)
{
    var pipe = new NamedPipeServerStream(name, PipeDirection.InOut, 8, PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
    await pipe.WaitForConnectionAsync();
    _ = Handle(pipe);
}

string KindLabel(string? kind, JsonNode? action) => kind switch
{
    "click" => "Clicking",
    "double_click" => "Double-clicking",
    "drag" => "Dragging",
    "draw" => "Drawing",
    "move" => "Moving the pointer",
    "scroll" => "Scrolling",
    "keypress" => "Pressing keys",
    "type" => "Typing" + Snippet(action?["text"]?.GetValue<string>()),
    "wait" => "Waiting",
    _ => "Working",
};

string Snippet(string? text) => string.IsNullOrWhiteSpace(text) ? "" : " “" + (text.Length > 22 ? text[..22] + "…" : text).Replace('\n', ' ').Replace('\r', ' ') + "”";

async Task Handle(NamedPipeServerStream pipe)
{
    using (pipe)
    using (var limit = new CancellationTokenSource(TimeSpan.FromSeconds(70)))
    {
        try
        {
            var prefix = new byte[4];
            await pipe.ReadExactlyAsync(prefix, limit.Token);
            var length = BitConverter.ToInt32(prefix);
            if (length < 1 || length > 4194304) return;
            var bytes = new byte[length];
            await pipe.ReadExactlyAsync(bytes, limit.Token);
            var envelope = JsonNode.Parse(bytes)!;
            var id = envelope["id"]!.GetValue<string>();
            var deadline = envelope["deadline"]!.GetValue<long>();
            var payload = envelope["payload"]!.GetValue<string>();
            var mac = Convert.FromHexString(envelope["mac"]!.GetValue<string>());
            var expected = HMACSHA256.HashData(Encoding.UTF8.GetBytes(secret), Encoding.UTF8.GetBytes($"{id}\n{deadline}\n{payload}"));
            var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            // The signature covers the deadline, so the payload is trusted before its kind selects the window.
            if (!CryptographicOperations.FixedTimeEquals(expected, mac) || deadline < now) return;
            var request = JsonNode.Parse(payload)!;
            var action = request["action"];
            var gesture = request["op"]?.GetValue<string>() == "visual_action" && action?["kind"]?.GetValue<string>() is "draw" or "drag";
            if (deadline > now + (gesture ? GestureWindowMs : ShortWindowMs) || !seen.TryAdd(id, deadline)) return;
            foreach (var entry in seen.Where(entry => entry.Value < now)) seen.TryRemove(entry.Key, out _);
            foreach (var entry in stopped.Where(entry => entry.Value < now)) stopped.TryRemove(entry.Key, out _);
            foreach (var entry in snapshots.Where(entry => entry.Value.Expires < now)) snapshots.TryRemove(entry.Key, out _);
            var task = request["task_id"]?.GetValue<string>() ?? id;
            request["task_id"] = task;
            var op = request["op"]!.GetValue<string>();
            JsonNode response;
            if (op == "overlay")
            {
                overlay?.Set(request["phase"]?.GetValue<string>() ?? "hidden", request["message"]?.GetValue<string>()?[..Math.Min(80, request["message"]!.GetValue<string>().Length)] ?? "");
                response = JsonNode.Parse("{\"ok\":true,\"result\":{\"overlay\":" + (overlay is null ? "false" : "true") + "}}")!;
            }
            else if (op == "stop")
            {
                fineSnapshots.Stop(task);
                foreach (var entry in snapshots.Where(entry => entry.Value.Task == task)) snapshots.TryRemove(entry.Key, out _);
                stopped[task] = now + 360000;
                overlay?.Stop();
                if (active.TryGetValue(task, out var cancel)) await cancel.CancelAsync();
                if (completed.TryGetValue(task, out var finished)) await finished.Task.WaitAsync(limit.Token);
                response = JsonNode.Parse("{\"ok\":true,\"result\":{\"stopped\":true}}")!;
            }
            else
            {
                await permits.WaitAsync(limit.Token);
                var mutates = op is "action" or "fine_action" or "visual_action" or "launch";
                var ownsInput = false;
                try
                {
                    if (mutates) { await inputPermit.WaitAsync(limit.Token); ownsInput = true; }
                    var visual = op == "visual_action";
                    var kind = action?["kind"]?.GetValue<string>();
                    var needsSnapshot = visual && kind is not "screenshot" and not "wait";
                    (string Target, string Task, long Expires, JsonNode Frame) receipt = default;
                    if (needsSnapshot && (!snapshots.TryRemove(request["snapshot_id"]?.GetValue<string>() ?? "", out receipt)
                        || receipt.Expires < DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() || receipt.Target != request["target"]?.ToJsonString() || receipt.Task != task))
                        response = JsonNode.Parse("{\"ok\":false,\"error\":\"SNAPSHOT_EXPIRED\"}")!;
                    else
                    {
                        if (needsSnapshot) request["frame"] = receipt.Frame!.DeepClone();
                        else request.AsObject().Remove("frame");
                        if (overlay is not null)
                        {
                            var title = request["target"]?["label"]?.GetValue<string>() ?? "";
                            if (op == "visual_state") overlay.Set("observing", title.Length > 0 ? "Looking at " + (title.Length > 34 ? title[..34] + "…" : title) : "");
                            else if (visual && needsSnapshot) overlay.Set("acting", KindLabel(kind, action));
                            // The overlay replaces the brief static flash the worker would otherwise show.
                            if (visual) request["feedback"] = false;
                        }
                        if (op == "fine_action")
                        {
                            try { request = fineSnapshots.Bind(request); }
                            catch (InvalidOperationException ex)
                            {
                                response = new JsonObject { ["ok"] = false, ["error"] = ex.Message };
                                await Reply(response);
                                return;
                            }
                        }
                        response = await Run(request, task, deadline, limit.Token, gesture || kind is "click" or "double_click");
                        if (op == "snapshot" && response["ok"]?.GetValue<bool>() == true)
                            response = stopped.ContainsKey(task)
                                ? new JsonObject { ["ok"] = false, ["error"] = "SNAPSHOT_EXPIRED" }
                                : new JsonObject { ["ok"] = true, ["result"] = fineSnapshots.Publish(request, response["result"]!) };
                        if (overlay is not null && (visual || op == "visual_state")) overlay.Set("thinking", "");
                        if (response["ok"]?.GetValue<bool>() == true && (op is "visual_state" or "visual_action"))
                        {
                            var state = op == "visual_state" ? response["result"] : response["result"]?["after"];
                            if (state?["capture"] is JsonNode capture && snapshots.Count < 64 && !stopped.ContainsKey(task))
                            {
                                var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(24));
                                var frame = capture.DeepClone();
                                frame["focused_element_id"] = state["observation"]?["focused_element_id"]?.DeepClone();
                                frame["elements"] = state["observation"]?["elements"]?.DeepClone();
                                snapshots[token] = (request["target"]!.ToJsonString(), task, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 30000, frame);
                                state["snapshot_id"] = token;
                            }
                        }
                    }
                }
                finally { if (ownsInput) inputPermit.Release(); permits.Release(); }
            }
            await Reply(response);
            async Task Reply(JsonNode value)
            {
            var output = Encoding.UTF8.GetBytes(value.ToJsonString());
            if (output.Length > 4194304) throw new InvalidOperationException("Result too large");
            await pipe.WriteAsync(BitConverter.GetBytes(output.Length), limit.Token);
            await pipe.WriteAsync(output, limit.Token);
            await pipe.FlushAsync(limit.Token);
            }
        }
        catch (Exception) { /* Connection failure is an unknown outcome, never success. */ }
    }
}

async Task<JsonNode> Run(JsonNode request, string task, long deadline, CancellationToken outer, bool pointerGesture)
{
    using var cancel = CancellationTokenSource.CreateLinkedTokenSource(outer);
    cancel.CancelAfter(TimeSpan.FromMilliseconds(Math.Max(1, deadline - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())));
    if (!active.TryAdd(task, cancel)) return JsonNode.Parse("{\"ok\":false,\"error\":\"WORKER_BUSY\"}")!;
    var finished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    completed[task] = finished;
    using var process = new Process { StartInfo = new ProcessStartInfo(worker) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true } };
    // Workers need no provider or launch credentials.
    foreach (var key in process.StartInfo.Environment.Keys.Where(k => k.EndsWith("_KEY") || k.Contains("TOKEN") || k.Contains("SECRET")).ToArray()) process.StartInfo.Environment.Remove(key);
    // The worker reports gesture progress on stderr; the overlay turns it into words ("Drawing 3/9").
    process.ErrorDataReceived += (_, e) =>
    {
        try
        {
            if (e.Data is not { Length: > 0 and < 500 } line || overlay is null) return;
            if (JsonNode.Parse(line)?["progress"] is JsonNode progress)
                overlay.Progress($"{progress["label"]?.GetValue<string>()} {progress["index"]?.GetValue<int>()}/{progress["total"]?.GetValue<int>()}");
        }
        catch (Exception) { /* Progress is cosmetic. */ }
    };
    var killed = false;
    try
    {
        if (stopped.ContainsKey(task)) cancel.Cancel();
        cancel.Token.ThrowIfCancellationRequested();
        process.Start();
        process.BeginErrorReadLine();
        await process.StandardInput.WriteLineAsync(request.ToJsonString().AsMemory(), cancel.Token);
        process.StandardInput.Close();
        var line = await process.StandardOutput.ReadLineAsync(cancel.Token);
        await process.WaitForExitAsync(cancel.Token);
        return line is null || line.Length > 4194304 ? JsonNode.Parse("{\"ok\":false,\"error\":\"WORKER_FAILED\"}")! : JsonNode.Parse(line)!;
    }
    catch (OperationCanceledException) { return JsonNode.Parse("{\"ok\":false,\"error\":\"UNKNOWN_EFFECT\"}")!; }
    catch (Exception) { return JsonNode.Parse("{\"ok\":false,\"error\":\"WORKER_FAILED\"}")!; }
    finally
    {
        try { if (process.Id > 0 && !process.HasExited) { killed = true; process.Kill(true); await process.WaitForExitAsync(); } } catch (InvalidOperationException) { }
        // A worker stopped mid-gesture must never leave a synthetic mouse button held down.
        if (killed && pointerGesture) Native.ReleaseMouse();
        active.TryRemove(task, out _);
        finished.TrySetResult();
        completed.TryRemove(task, out _);
    }
}
