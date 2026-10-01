// Physical pixel centres avoid bias toward a neighbouring pixel after normalization.
internal static class PointerGeometry
{
    public static int Normalize(int pixel, int origin, int extent)
    {
        var offset = (long)pixel - origin;
        if (extent is <= 0 or > 65536 || offset < 0 || offset >= extent)
            throw new InvalidOperationException("POINT_OUTSIDE_DESKTOP");
        return (int)(((2 * offset + 1) * 65536) / (2 * extent));
    }

    public static int FromScreenshot(int pixel, int screenshotExtent, int origin, int physicalExtent)
    {
        if (screenshotExtent <= 0 || physicalExtent <= 0 || pixel < 0 || pixel >= screenshotExtent)
            throw new InvalidOperationException("POINT_OUTSIDE_SNAPSHOT");
        return checked(origin + (int)(((2L * pixel + 1) * physicalExtent) / (2L * screenshotExtent)));
    }

    /// <summary>Smooth acceleration and deceleration so the pointer visibly travels instead of teleporting.</summary>
    public static double EaseInOut(double t)
    {
        t = Math.Clamp(t, 0, 1);
        return t < 0.5 ? 4 * t * t * t : 1 - Math.Pow(-2 * t + 2, 3) / 2;
    }

    /// <summary>Travel time for a pointer glide: quick for short hops, never slower than half a second.</summary>
    public static int GlideMilliseconds(double distance) => (int)Math.Clamp(140 + distance * 0.32, 140, 520);

    /// <summary>
    /// Every waypoint plus evenly spaced points between them, at most <paramref name="spacing"/> pixels
    /// apart. Applications draw a continuous stroke only when they receive many small moves.
    /// </summary>
    public static List<(int X, int Y)> Densify(IReadOnlyList<(int X, int Y)> waypoints, int spacing)
    {
        if (spacing < 1) throw new ArgumentOutOfRangeException(nameof(spacing));
        var output = new List<(int X, int Y)>();
        if (waypoints.Count == 0) return output;
        output.Add(waypoints[0]);
        for (var index = 1; index < waypoints.Count; index++)
        {
            var start = waypoints[index - 1];
            var end = waypoints[index];
            var distance = Math.Sqrt(Math.Pow(end.X - start.X, 2) + Math.Pow(end.Y - start.Y, 2));
            var steps = Math.Max(1, (int)Math.Ceiling(distance / spacing));
            for (var step = 1; step <= steps; step++)
            {
                var point = (start.X + (int)Math.Round((end.X - start.X) * (double)step / steps),
                    start.Y + (int)Math.Round((end.Y - start.Y) * (double)step / steps));
                if (point != output[^1]) output.Add(point);
            }
        }
        return output;
    }
}
