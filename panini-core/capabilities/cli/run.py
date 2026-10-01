"""Source-tree entry point; installed packages expose the panini-cli command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from panini_cli.cli import main

if __name__ == "__main__":
    main()
