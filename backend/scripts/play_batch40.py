"""Batch 40 isolated runs using the existing HTTP play/probe/export driver."""

import os

os.environ["COC_PLAY_BASE"] = "data/prepared/zhuishuren/batch-40"
os.environ["COC_PLAY_PORT"] = "8040"

from scripts.play_batch39 import main  # noqa: E402

if __name__ == "__main__":
    main()
