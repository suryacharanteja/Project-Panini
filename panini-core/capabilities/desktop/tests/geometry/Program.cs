var cases = 0;
foreach (var extent in new[] { 1, 800, 1366, 1920, 2560, 3840, 7680, 15360, 65536 })
foreach (var origin in new[] { 0, -3840, 1920 })
for (var offset = 0; offset < extent; offset++)
{
    var normalized = PointerGeometry.Normalize(origin + offset, origin, extent);
    var decoded = normalized * (long)extent / 65536;
    if (decoded != offset) throw new Exception($"Pixel mismatch {extent}/{offset}: {decoded}");
    cases++;
}
foreach (var extent in new[] { 800, 1366, 1920, 3840, 7680 })
{
    var scaled = Math.Min(extent, 1280);
    for (var x = 0; x < scaled; x++)
    {
        var physical = PointerGeometry.FromScreenshot(x, scaled, -1920, extent);
        var ideal = -1920 + (x + 0.5) * extent / scaled;
        if (physical > ideal || ideal - physical >= 1) throw new Exception("Screenshot mapping bias");
        cases++;
    }
}
// Strokes: every waypoint survives, spacing is bounded, and nothing is duplicated.
var waypoints = new List<(int X, int Y)> { (0, 0), (100, 0), (100, 60), (100, 60), (40, 60) };
var dense = PointerGeometry.Densify(waypoints, 4);
foreach (var waypoint in waypoints.Distinct()) if (!dense.Contains(waypoint)) throw new Exception($"Waypoint lost {waypoint}");
for (var i = 1; i < dense.Count; i++)
{
    var d = Math.Sqrt(Math.Pow(dense[i].X - dense[i - 1].X, 2) + Math.Pow(dense[i].Y - dense[i - 1].Y, 2));
    if (d > 4.01 || d == 0) throw new Exception($"Bad stroke spacing {d} at {i}");
    cases++;
}
if (dense[0] != (0, 0) || dense[^1] != (40, 60)) throw new Exception("Stroke endpoints changed");
if (PointerGeometry.Densify([], 4).Count != 0 || PointerGeometry.Densify([(5, 5)], 4).Count != 1) throw new Exception("Degenerate strokes");
// Easing: monotonic from 0 to 1, symmetric, and clamped.
double previous = -1;
for (var i = 0; i <= 100; i++)
{
    var eased = PointerGeometry.EaseInOut(i / 100.0);
    if (eased < previous || eased < 0 || eased > 1) throw new Exception("Easing is not monotonic");
    previous = eased; cases++;
}
if (PointerGeometry.EaseInOut(0) != 0 || PointerGeometry.EaseInOut(1) != 1 || Math.Abs(PointerGeometry.EaseInOut(.5) - .5) > 1e-9) throw new Exception("Easing endpoints");
if (PointerGeometry.EaseInOut(-3) != 0 || PointerGeometry.EaseInOut(9) != 1) throw new Exception("Easing clamp");
if (PointerGeometry.GlideMilliseconds(0) < 140 || PointerGeometry.GlideMilliseconds(99999) > 520) throw new Exception("Glide duration bounds");
Console.WriteLine($"PASS: {cases} pixel normalization, scaled-coordinate, stroke and easing checks");
