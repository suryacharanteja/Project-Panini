# PANINI Brand Package

All files in this package are generated directly from your two source
images — nothing was redrawn or recreated. The icon is a pixel-crop of
the network/question-mark graphic from your logo file, with the
"PANINI" wordmark and tagline removed. Design is unchanged.

## Folder structure

```
panini-brand-package/
├── source/
│   ├── panini-logo-master.png   ← your original uploaded logo (full, untouched)
│   └── panini-icon-master.png   ← icon graphic, cropped out of the logo (no text)
│
├── logo/
│   ├── panini-logo-full.png     ← original resolution (2816×1536)
│   ├── panini-logo-1600w.png    ← 1600px wide
│   ├── panini-logo-800w.png     ← 800px wide
│   └── panini-logo-400w.png     ← 400px wide
│
├── icon/
│   ├── png/
│   │   ├── panini-icon-16.png
│   │   ├── panini-icon-32.png
│   │   ├── panini-icon-48.png
│   │   ├── panini-icon-64.png
│   │   ├── panini-icon-96.png
│   │   ├── panini-icon-128.png
│   │   ├── panini-icon-144.png
│   │   ├── panini-icon-192.png
│   │   ├── panini-icon-256.png
│   │   ├── panini-icon-512.png
│   │   └── panini-icon-1024.png
│   │
│   ├── ico/
│   │   └── panini-icon.ico      ← multi-resolution (16/32/48/64/128/256), for Windows apps
│   │
│   └── favicon/
│       ├── favicon.ico          ← 16+32px, for websites
│       ├── favicon-16.png
│       ├── favicon-32.png
│       └── apple-touch-icon-180.png
```

## Notes on the icon crop

The icon has a soft glow effect that blends into the dark background —
this is baked into the artwork itself (not a solid color), so a clean
transparent cutout isn't possible without redrawing the graphic, which
would change the design. Every icon file here keeps the original dark
background intact, exactly as it appears in your source logo. If you
need a true transparent-background version, that requires the original
layered design file (e.g. Illustrator/Photoshop/Figma source) rather
than a flattened image.

## Quick use

- **Windows app icon** → `icon/ico/panini-icon.ico`
- **Website favicon** → `icon/favicon/favicon.ico` + the two PNGs
- **iOS home screen icon** → `icon/favicon/apple-touch-icon-180.png`
- **App store / high-res listing** → `icon/png/panini-icon-512.png` or `-1024.png`
- **Website header logo** → `logo/panini-logo-800w.png` (or 1600w for retina)
- **Full-resolution logo** → `logo/panini-logo-full.png`
