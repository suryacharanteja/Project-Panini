// Adapted from pi-computer-use capture.rs. Copyright (c) 2026 Zane Chee.
// Adapted from pi-computer-use 0.5.1 (MIT); see licenses/pi-computer-use.MIT.txt.
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class WindowCapture
{
    public static (int X, int Y, int Width, int Height) Bounds(IntPtr window)
    {
        if (IsIconic(window) || !GetWindowRect(window, out var rect)) throw new InvalidOperationException("CAPTURE_UNAVAILABLE");
        return (rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top);
    }

    public static bool Matches(JsonNode expected, JsonNode actual, IReadOnlyList<(int X, int Y)> points)
    {
        if (expected["image_base64"]!.GetValue<string>() == actual["image_base64"]!.GetValue<string>()) return true;
        BitmapSource Decode(JsonNode frame)
        {
            using var stream = new System.IO.MemoryStream(Convert.FromBase64String(frame["image_base64"]!.GetValue<string>()));
            return new FormatConvertedBitmap(BitmapFrame.Create(stream, BitmapCreateOptions.None, BitmapCacheOption.OnLoad), PixelFormats.Bgr24, null, 0);
        }
        var a = Decode(expected); var b = Decode(actual);
        if (a.PixelWidth != b.PixelWidth || a.PixelHeight != b.PixelHeight) return false;
        var sourceX = expected["source_x"]!.GetValue<int>(); var sourceY = expected["source_y"]!.GetValue<int>();
        var sx = (double)a.PixelWidth / expected["source_width"]!.GetValue<int>();
        var sy = (double)a.PixelHeight / expected["source_height"]!.GetValue<int>();
        var focused = AutomationElement.FocusedElement;
        var region = new System.Windows.Rect(0, 0, a.PixelWidth, a.PixelHeight);
        if (points.Count > 0)
            region = new System.Windows.Rect((points.Min(p => p.X) - sourceX) * sx - 32, (points.Min(p => p.Y) - sourceY) * sy - 32,
                (points.Max(p => p.X) - points.Min(p => p.X)) * sx + 65, (points.Max(p => p.Y) - points.Min(p => p.Y)) * sy + 65);
        else if (focused is not null && !focused.Current.IsPassword && string.Join('.', focused.GetRuntimeId()) == expected["focused_element_id"]?.GetValue<string>())
        {
            var bounds = focused.Current.BoundingRectangle;
            region = new System.Windows.Rect((bounds.X - sourceX) * sx, (bounds.Y - sourceY) * sy, bounds.Width * sx, bounds.Height * sy);
        }
        var stride = a.PixelWidth * 3;
        var ap = new byte[stride * a.PixelHeight]; var bp = new byte[ap.Length];
        a.CopyPixels(ap, stride, 0); b.CopyPixels(bp, stride, 0);
        var left = a.PixelWidth; var top = a.PixelHeight; var right = -1; var bottom = -1;
        for (var y = 0; y < a.PixelHeight; y++) for (var x = 0; x < a.PixelWidth; x++)
        {
            if (!region.Contains(x, y)) continue;
            var i = y * stride + x * 3;
            if (ap[i] == bp[i] && ap[i + 1] == bp[i + 1] && ap[i + 2] == bp[i + 2]) continue;
            left = Math.Min(left, x); top = Math.Min(top, y); right = Math.Max(right, x); bottom = Math.Max(bottom, y);
        }
        if (right < 0) return true;
        if (focused is null || focused.Current.IsPassword || focused.Current.ControlType != ControlType.Edit
            || string.Join('.', focused.GetRuntimeId()) != expected["focused_element_id"]?.GetValue<string>()) return false;
        var rect = focused.Current.BoundingRectangle;
        return right - left < 4 && bottom - top < 50 && left >= (rect.X - sourceX) * sx
            && right < (rect.Right - sourceX) * sx && top >= (rect.Y - sourceY) * sy && bottom < (rect.Bottom - sourceY) * sy;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr GetWindowDC(IntPtr window);
    [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr window, IntPtr dc);
    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
    [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleBitmap(IntPtr dc, int width, int height);
    [DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr dc);

    public static object Capture(IntPtr window)
    {
        if (IsIconic(window) || !GetWindowRect(window, out var rect)) throw new InvalidOperationException("CAPTURE_UNAVAILABLE");
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0 || width > 8192 || height > 8192 || (long)width * height > 16000000)
            throw new InvalidOperationException("CAPTURE_BOUNDS_UNSUPPORTED");
        var dc = GetWindowDC(window);
        if (dc == IntPtr.Zero) throw new InvalidOperationException("CAPTURE_UNAVAILABLE");
        IntPtr memory = IntPtr.Zero, bitmap = IntPtr.Zero, previous = IntPtr.Zero;
        try
        {
            memory = CreateCompatibleDC(dc);
            bitmap = CreateCompatibleBitmap(dc, width, height);
            if (memory == IntPtr.Zero || bitmap == IntPtr.Zero) throw new InvalidOperationException("CAPTURE_UNAVAILABLE");
            previous = SelectObject(memory, bitmap);
            if (previous == IntPtr.Zero || previous == new IntPtr(-1) || !PrintWindow(window, memory, 2))
                throw new InvalidOperationException("CAPTURE_UNAVAILABLE");
            var source = System.Windows.Interop.Imaging.CreateBitmapSourceFromHBitmap(bitmap, IntPtr.Zero, System.Windows.Int32Rect.Empty, BitmapSizeOptions.FromEmptyOptions());
            var opaque = new FormatConvertedBitmap(source, PixelFormats.Bgr24, null, 0);
            BitmapSource output = opaque;
            var scale = Math.Min(1, 1280.0 / Math.Max(width, height));
            if (scale < 1) output = new TransformedBitmap(opaque, new ScaleTransform(scale, scale));
            var pixels = new byte[output.PixelWidth * output.PixelHeight * 3];
            output.CopyPixels(pixels, output.PixelWidth * 3, 0);
            if (pixels.All(value => value < 8)) throw new InvalidOperationException("CAPTURE_BLANK");
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(output));
            using var stream = new System.IO.MemoryStream();
            encoder.Save(stream);
            if (stream.Length > 600000) throw new InvalidOperationException("CAPTURE_TOO_LARGE");
            return new { image_base64 = Convert.ToBase64String(stream.ToArray()), mime_type = "image/png", width = output.PixelWidth, height = output.PixelHeight, source_width = width, source_height = height, source_x = rect.Left, source_y = rect.Top, scale, captured_at = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), method = "PrintWindow" };
        }
        finally
        {
            if (previous != IntPtr.Zero && previous != new IntPtr(-1)) SelectObject(memory, previous);
            if (bitmap != IntPtr.Zero) DeleteObject(bitmap);
            if (memory != IntPtr.Zero) DeleteDC(memory);
            ReleaseDC(window, dc);
        }
    }
}
