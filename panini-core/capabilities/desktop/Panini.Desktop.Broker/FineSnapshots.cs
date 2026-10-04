using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;

// UIA runtime IDs are retained here; callers receive observation-scoped refs and opaque identities.
internal sealed class FineSnapshots
{
    private sealed record Entry(string Target, string Task, long Expires, long ObservedAt, string Title, Dictionary<string, JsonNode> Nodes);
    private readonly ConcurrentDictionary<string, Entry> entries = new();
    private long sequence;

    public void Stop(string task)
    {
        foreach (var row in entries.Where(row => row.Value.Task == task)) entries.TryRemove(row.Key, out _);
    }

    public JsonNode Publish(JsonNode request, JsonNode result)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        foreach (var row in entries.Where(row => row.Value.Expires < now)) entries.TryRemove(row.Key, out _);
        Stop(request["task_id"]!.GetValue<string>()); // A new observation invalidates this run's older refs.
        if (entries.Count >= 64) throw new InvalidOperationException("SNAPSHOT_CAPACITY");
        var nodes = new Dictionary<string, JsonNode>();
        var publicNodes = new JsonArray();
        string? focusedRef = null;
        foreach (var node in result["nodes"]!.AsArray())
        {
            var reference = "w" + Interlocked.Increment(ref sequence);
            nodes[reference] = node!.DeepClone();
            var view = node.DeepClone().AsObject();
            view["identity"] = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(node.ToJsonString())));
            var anchor = new JsonObject();
            foreach (var key in new[] { "element_id", "automation_id", "name", "type", "bounds" }) anchor[key] = node[key]!.DeepClone();
            view["anchor"] = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(anchor.ToJsonString())));
            view.Remove("element_id");
            view.Remove("automation_id");
            view["ref"] = reference;
            if (view["focused"]?.GetValue<bool>() == true) focusedRef = reference;
            publicNodes.Add(view);
        }
        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(24));
        entries[token] = new(request["target"]!.ToJsonString(), request["task_id"]!.GetValue<string>(), now + 30000,
            result["observed_at"]!.GetValue<long>(), result["title"]!.GetValue<string>(), nodes);
        var output = result.DeepClone().AsObject();
        output.Remove("focused_element_id");
        output["focused_ref"] = focusedRef;
        output["nodes"] = publicNodes;
        output["snapshot_id"] = token;
        return output;
    }

    public JsonNode Bind(JsonNode request)
    {
        if (!entries.TryRemove(request["snapshot_id"]?.GetValue<string>() ?? "", out var entry)
            || entry.Expires < DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            || entry.Target != request["target"]?.ToJsonString() || entry.Task != request["task_id"]?.GetValue<string>()
            || !entry.Nodes.TryGetValue(request["ref"]?.GetValue<string>() ?? "", out var node))
            throw new InvalidOperationException("SNAPSHOT_EXPIRED");
        if (node["states"]!.AsArray().Any(s => s!.GetValue<string>() is "password" or "disabled"))
            throw new InvalidOperationException("ELEMENT_UNAVAILABLE");
        var action = request["action"]!.DeepClone().AsObject();
        if (action["kind"]?.GetValue<string>() is not ("click" or "double_click" or "invoke" or "focus" or "set_value" or "fine_type" or "select" or "toggle" or "scroll" or "keypress"))
            throw new InvalidOperationException("UNSUPPORTED_ACTION");
        action["element_id"] = node["element_id"]!.DeepClone();
        action["expected_name"] = node["name"]!.DeepClone();
        action["expected_automation_id"] = node["automation_id"]!.DeepClone();
        action["expected_type"] = node["type"]!.DeepClone();
        action["expected_bounds"] = node["bounds"]!.DeepClone();
        action["expected_states"] = node["states"]!.DeepClone();
        action["expected_value"] = node["value"]?.DeepClone();
        var bound = request.DeepClone().AsObject();
        bound.Remove("snapshot_id");
        bound.Remove("ref");
        bound["op"] = "action";
        bound["observed_at"] = entry.ObservedAt;
        bound["expected_window_title"] = entry.Title;
        bound["action"] = action;
        return bound;
    }
}
