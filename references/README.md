# References

Upstream snapshots PANINI v2 learns from. They are **never imported, bundled or run** by PANINI; ported
patterns and code live in PANINI's own tree with attribution. See
[docs/panini-enhancements/harvest-map.md](../docs/panini-enhancements/harvest-map.md).

The snapshot folders are git-ignored; this README records what they are.

| Folder | Upstream | Snapshot | Licence | Notes |
| --- | --- | --- | --- | --- |
| `opendots/` | [CopilotKit/OpenDots](https://github.com/CopilotKit/OpenDots) | 2026-10-01 (copied, no `.git`) | MIT | Pins OpenBot `b6932d31a8d6e7896c15139dfc27a6c6911deb27` in `compose.computers.yml`. Demo media removed locally. |

To restore a snapshot, clone the upstream repository into the folder.
