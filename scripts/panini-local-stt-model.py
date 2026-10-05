"""Fetch and verify the pinned MIT-licensed offline speech model for packaging.

The package copies only these verified files. Runtime transcription never calls
this script or the Hugging Face Hub.
"""

import hashlib
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "artifacts" / "model-cache" / "faster-whisper-base"
REPOSITORY = "Systran/faster-whisper-base"
REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
FILES = {
    "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
    "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
    "README.md": "b922acef3616e27b1d416260b63ba7114c732f0315012006781af2da69cd1ebf",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}


def verified() -> bool:
    return all(
        (DEST / name).is_file() and hashlib.sha256((DEST / name).read_bytes()).hexdigest() == digest
        for name, digest in FILES.items()
    )


if __name__ == "__main__":
    if not verified():
        snapshot_download(
            REPOSITORY,
            revision=REVISION,
            local_dir=DEST,
            allow_patterns=list(FILES),
        )
    if not verified():
        raise SystemExit("PANINI offline speech model failed pinned SHA-256 verification")
    print(f"PANINI_LOCAL_STT_MODEL_VERIFIED {REPOSITORY}@{REVISION} {len(FILES)} files")
